# ============================================================
#  scraper/downloader.py v7
#  + поддержка 223-ФЗ (другая структура URL)
# ============================================================

import time
import re
import shutil
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
from loguru import logger

import config
from scraper.browser import safe_get, random_delay, wait_for


ICON_ALT_TO_EXT = {
    "Microsoft Excel Document":          ".xls",
    "Microsoft Excel 2007-2019 (.xlsx)": ".xlsx",
    "Microsoft Word Document":           ".doc",
    "Microsoft Word 2007-2019":          ".docx",
    "Adobe Acrobat Document":            ".pdf",
    "RAR Archive Document":              ".rar",
    "ZIP Archive Document":              ".zip",
    "7-Zip Archive Document":            ".7z",
    "Rich Text Format Document":         ".rtf",
    "Open Document Text":                ".odt",
    "Open Document Spreadsheet":         ".ods",
    "Text Document":                     ".txt",
}

DOC_KEYWORDS = {
    "ТЗ":               ["техническое задание", "тз_", " тз ", "описание объекта", "техзадание"],
    "проект_контракта": ["проект контракта", "проект государственного контракта",
                         "проект гк", "проект договора"],
    "условия":          ["условия участия", "информационная карта", "требования",
                         "извещение", "документация"],
    "обоснование_нмц":  ["обоснование начальной", "онмцк", "нмцк", "расчет нмцк", "расчёт нмцк"],
    "протокол":         ["протокол"],
    "прочее":           [],
}

def classify_doc(text: str) -> str:
    n = text.lower()
    for doc_type, keywords in DOC_KEYWORDS.items():
        if any(kw in n for kw in keywords):
            return doc_type
    return "прочее"


def safe_filename(name: str, ext: str = "") -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(". ")
    if not name:
        name = "document"
    if ext and not name.lower().endswith(ext.lower()):
        name = name + ext
    return name[:200]


def _build_docs_url(tender_url: str, reg_number: str) -> str:
    """
    Строит URL вкладки «Документы» с учётом типа закупки:

    44-ФЗ:  .../epz/order/notice/ea20/view/common-info.html
            → .../epz/order/notice/ea20/view/documents.html

    223-ФЗ: .../223/purchase/public/purchase/info/common-info.html
            → .../223/purchase/public/purchase/info/documents.html
    """
    if not tender_url:
        return (
            f"https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html"
            f"?regNumber={reg_number}"
        )

    # 223-ФЗ
    if "/223/purchase/" in tender_url:
        url = re.sub(r'/info/[^/?]+\.html', '/info/documents.html', tender_url)
        if "documents.html" not in url:
            # Другой вариант пути 223-ФЗ
            url = re.sub(r'/purchase/[^/?]+\.html', '/purchase/documents.html', tender_url)
        logger.debug(f"  223-ФЗ docs URL: {url}")
        return url

    # 44-ФЗ
    url = re.sub(r"/view/[^/?]+\.html", "/view/documents.html", tender_url)
    if "documents.html" not in url:
        url = tender_url
    return url


def _snapshot_downloads(download_dir: Path) -> set:
    return set(download_dir.glob("*"))


def _wait_download_complete(before: set,
                             download_dir: Path,
                             timeout: int = config.DOWNLOAD_TIMEOUT) -> Path | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        after = set(download_dir.glob("*"))
        new_files = after - before
        complete = [f for f in new_files
                    if not f.name.endswith(".crdownload")
                    and not f.name.startswith(".")]
        if complete:
            return max(complete, key=lambda f: f.stat().st_mtime)
        crdown = [f for f in new_files if f.name.endswith(".crdownload")]
        if crdown:
            logger.debug(f"    Скачивается: {[f.name for f in crdown]}")
    return None


def parse_doc_blocks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    docs = []
    for block in soup.select("div.attachment"):
        try:
            img = block.select_one("img[alt]")
            alt = img.get("alt", "").strip() if img else ""
            ext = ICON_ALT_TO_EXT.get(alt, "")
            if not ext and img:
                src = img.get("src", "")
                m = re.search(r'/type/(\w+)\.svg', src)
                if m:
                    ext = "." + m.group(1).lower()

            span = block.select_one("span.section__value")
            if not span:
                continue
            a = span.select_one("a[href]")
            if not a:
                continue

            href = a.get("href", "")
            title = a.get("title", "").strip()
            label = a.get_text(strip=True)

            if not href or ("filestore" not in href and "uid=" not in href):
                continue
            if not href.startswith("http"):
                href = "https://zakupki.gov.ru" + href

            uid = ""
            m = re.search(r'uid=([A-Fa-f0-9]+)', href)
            if m:
                uid = m.group(1)

            if title:
                has_ext = bool(re.search(r'\.\w{2,5}\s*$', title))
                filename = safe_filename(title, "" if has_ext else ext)
            else:
                filename = safe_filename(label or f"doc_{uid[:8]}", ext)

            doc_type = classify_doc(label + " " + title)
            if uid and any(d.get("uid") == uid for d in docs):
                continue

            docs.append({"uid": uid, "href": href, "filename": filename,
                         "ext": ext, "label": label or title,
                         "alt": alt, "doc_type": doc_type})
        except Exception as e:
            logger.warning(f"  Ошибка парсинга блока: {e}")
    return docs


def _click_and_download(driver: webdriver.Chrome,
                        doc: dict,
                        download_dir: Path,
                        tender_dir: Path) -> dict | None:
    uid = doc["uid"]
    href = doc["href"]

    before = _snapshot_downloads(download_dir)
    logger.debug(f"    Файлов в docs/ до клика: {len(before)}")

    try:
        xpath = (
            f"//span[contains(@class,'section__value')]"
            f"//a[contains(@href,'uid={uid}')]"
        )
        link_el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", link_el)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", link_el)

    except TimeoutException:
        logger.debug(f"    Xpath не найден, JS redirect: {href}")
        driver.execute_script(f"window.location.href = '{href}';")
    except Exception as e:
        logger.warning(f"    Ошибка клика: {e}")
        return None

    time.sleep(2)

    dl_path = _wait_download_complete(before, download_dir, timeout=30)

    if not dl_path:
        current = set(download_dir.glob("*"))
        new = current - before
        logger.debug(f"    Новых файлов в docs/: {len(new)} → {[f.name for f in new]}")
        return None

    final_ext = dl_path.suffix.lower()
    if final_ext in (".crdownload", ""):
        final_ext = doc["ext"] or ".bin"

    filename = doc["filename"]
    if not filename.lower().endswith(final_ext):
        filename = safe_filename(filename, final_ext)

    dest = tender_dir / filename
    counter = 1
    while dest.exists():
        stem = Path(filename).stem
        suf = Path(filename).suffix
        dest = tender_dir / f"{stem}_{counter}{suf}"
        counter += 1

    shutil.move(str(dl_path), str(dest))
    size = dest.stat().st_size
    logger.info(f"  ✅ {dest.name} ({size // 1024} КБ)")
    return {"filename": dest.name, "filepath": str(dest),
            "file_size": size, "doc_type": doc["doc_type"]}


def download_tender_docs(driver: webdriver.Chrome,
                         tender: dict,
                         tender_id: int) -> list[dict]:
    reg_number = re.sub(r'[№#\s]', '', tender.get("purchase_number", "")).strip()
    tender_url = tender.get("url", "")

    if not reg_number and not tender_url:
        logger.warning(f"Нет номера и URL тендера id={tender_id}")
        return []

    docs_url = _build_docs_url(tender_url, reg_number)

    safe_num = re.sub(r"[^\w\-]", "_", reg_number or str(tender_id))
    tender_dir = config.DOCS_DIR / safe_num
    tender_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"📄 Тендер {safe_num} → {docs_url}")

    if not safe_get(driver, docs_url):
        logger.error(f"Не удалось открыть: {docs_url}")
        return []

    found = wait_for(driver, By.CSS_SELECTOR, "div.attachment", timeout=20)
    if not found:
        wait_for(driver, By.CLASS_NAME, "blockInfo", timeout=10)
    random_delay(1.0, 2.0)

    docs = parse_doc_blocks(driver.page_source)

    # Fallback для 44-ФЗ
    if not docs and reg_number and "/223/" not in docs_url:
        fallback = (
            f"https://zakupki.gov.ru/epz/order/notice/view/documents.html"
            f"?regNumber={reg_number}"
        )
        logger.info(f"  Fallback: {fallback}")
        if safe_get(driver, fallback):
            wait_for(driver, By.CSS_SELECTOR, "div.attachment", timeout=15)
            docs = parse_doc_blocks(driver.page_source)

    if not docs:
        logger.info(f"  Документов не найдено для {safe_num}")
        return []

    logger.info(f"  Найдено документов: {len(docs)}")
    downloaded = []

    for i, doc in enumerate(docs, 1):
        logger.info(f"  ⬇️  [{i}/{len(docs)}] [{doc['alt'] or doc['ext']}] {doc['label']}")

        result = _click_and_download(driver, doc, config.DOCS_DIR, tender_dir)
        if result:
            downloaded.append(result)
        else:
            logger.warning(f"  ⚠️  Не скачался: {doc['label']}")

        if driver.current_url.split("?")[0] != docs_url.split("?")[0]:
            safe_get(driver, docs_url)
            wait_for(driver, By.CSS_SELECTOR, "div.attachment", timeout=15)
            random_delay(1.0, 1.5)

    return downloaded
