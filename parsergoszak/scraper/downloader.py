

import time
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin, unquote, urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from bs4 import BeautifulSoup
from loguru import logger

import config
from scraper.browser import safe_get, random_delay, wait_for


# ── Типы документов ───────────────────────────────────────────────────────────

DOC_KEYWORDS = {
    "ТЗ":               ["техническое задание", "тз", "т.з.", "техзадание",
                         "описание объекта"],
    "проект_контракта": ["проект контракта", "проект государственного контракта",
                         "проект договора"],
    "условия":          ["условия участия", "документация", "извещение",
                         "информационная карта", "требования к содержанию",
                         "требования к заявке"],
    "обоснование_нмц":  ["обоснование начальной", "онмцк", "нмцк"],
    "протокол":         ["протокол"],
    "прочее":           [],
}

def classify_doc(name: str) -> str:
    """Определяет тип документа по названию (display name или filename)."""
    n = name.lower()
    for doc_type, keywords in DOC_KEYWORDS.items():
        if any(kw in n for kw in keywords):
            return doc_type
    return "прочее"


# ── Безопасное имя файла ─────────────────────────────────────────────────────

def safe_filename(name: str, uid: str, ext: str = "") -> str:
    """Превращает произвольное имя файла в безопасное для ФС."""
    # Убираем опасные символы
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    if not name:
        name = f"document_{uid[:8]}"
    if ext and not name.lower().endswith(ext.lower()):
        name = name + ext
    return name[:200]  # ограничиваем длину


# ── Извлечение расширения из имени файла ─────────────────────────────────────

def get_extension(filename: str) -> str:
    """Извлекает расширение из имени файла (например 'Проект.docx' → '.docx')."""
    m = re.search(r'\.(pdf|docx?|xlsx?|zip|rar|7z|rtf|odt|ods)$', filename, re.IGNORECASE)
    return m.group(0).lower() if m else ""


# ── Парсинг ссылок на документы ──────────────────────────────────────────────

def parse_doc_links(html: str, base_url: str) -> list[dict]:
    """
    Парсит страницу тендера ЕИС и извлекает ссылки на документы.

    ЕИС использует формат:
      href="https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=XXXX"
      title="Реальное имя файла.docx"

    Текст ссылки — человекочитаемое название документа.
    """
    soup = BeautifulSoup(html, "lxml")
    links = []

    # Основной паттерн: ссылки на filestore с uid
    for a in soup.select("a[href*='filestore'][href*='uid=']"):
        href  = a.get("href", "")
        title = a.get("title", "").strip()   # "Приложение №4. Проект контракта.doc"
        label = a.get_text(strip=True)       # "Проект государственного контракта"

        if not href:
            continue

        # Делаем абсолютный URL
        full_url = urljoin(base_url, href) if not href.startswith("http") else href

        # Извлекаем uid из URL
        uid = ""
        m = re.search(r'uid=([A-Fa-f0-9]+)', href)
        if m:
            uid = m.group(1)

        # Имя файла: берём из title (там реальное имя с расширением)
        # Если title пустой — используем label как имя
        if title:
            filename = safe_filename(title, uid)
            ext      = get_extension(title)
        else:
            ext      = ""
            filename = safe_filename(label or f"doc_{uid[:8]}", uid)

        # Тип документа: по label (человекочитаемому названию) — он точнее
        doc_type = classify_doc(label or title)

        # Дедупликация по uid
        if uid and any(l.get("uid") == uid for l in links):
            continue

        links.append({
            "url":      full_url,
            "filename": filename,
            "ext":      ext,
            "label":    label,
            "uid":      uid,
            "doc_type": doc_type,
        })
        logger.debug(f"  Найден документ: [{doc_type}] {label!r} → {filename}")

    # Запасной вариант: прямые ссылки на файлы (если вдруг есть)
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not re.search(r'\.(pdf|docx?|xlsx?|zip|rar|7z|rtf)(\?|$)', href, re.IGNORECASE):
            continue
        full_url = urljoin(base_url, href) if not href.startswith("http") else href
        # Проверяем, не добавили ли уже этот URL
        if any(l["url"] == full_url for l in links):
            continue
        filename = unquote(href.split("/")[-1].split("?")[0]) or "document"
        links.append({
            "url":      full_url,
            "filename": filename,
            "ext":      get_extension(filename),
            "label":    a.get_text(strip=True),
            "uid":      "",
            "doc_type": classify_doc(filename),
        })

    return links


# ── Ожидание завершения скачивания ───────────────────────────────────────────

def _wait_download_complete(download_dir: Path,
                             timeout: int = config.DOWNLOAD_TIMEOUT) -> Path | None:
    deadline = time.time() + timeout
    before   = set(download_dir.glob("*"))

    while time.time() < deadline:
        time.sleep(1)
        after     = set(download_dir.glob("*"))
        new_files = after - before
        complete  = [f for f in new_files
                     if not f.name.endswith(".crdownload")
                     and not f.name.startswith(".")]
        if complete:
            return max(complete, key=lambda f: f.stat().st_mtime)
    return None


# ── Основная функция скачивания ──────────────────────────────────────────────

def download_tender_docs(
    driver: webdriver.Chrome,
    tender: dict,
    tender_id: int,
) -> list[dict]:
    """
    Открывает страницу документов тендера, парсит и скачивает файлы.
    Возвращает список метаданных скачанных файлов.
    """
    tender_url = tender.get("url", "")
    if not tender_url:
        logger.warning(f"Нет URL тендера #{tender.get('purchase_number')}")
        return []

    # URL страницы документов: /view/documents.html
    reg_number = tender.get("purchase_number", "")
    if reg_number:
        # Строим прямой URL вкладки документов
        base = "https://zakupki.gov.ru/epz/order/notice/ea20/view/documents.html"
        docs_url = f"{base}?regNumber={reg_number}"
    else:
        docs_url = tender_url

    # Папка для документов этого тендера
    safe_num   = re.sub(r"[^\w\-]", "_", reg_number or str(tender_id))
    tender_dir = config.DOCS_DIR / safe_num
    tender_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"📄 Открываем документы тендера {safe_num}")

    if not safe_get(driver, docs_url):
        logger.error(f"Не удалось открыть страницу документов: {docs_url}")
        return []

    # Ждём загрузки блока с документами
    wait_for(driver, By.CSS_SELECTOR, "div.blockFilesTabDocs, div.attachment", timeout=20)
    random_delay(1.5, 2.5)

    html  = driver.page_source
    links = parse_doc_links(html, docs_url)

    if not links:
        logger.info(f"  Документов не найдено для {safe_num}")
        return []

    logger.info(f"  Найдено документов: {len(links)}")
    downloaded = []

    for doc in links:
        try:
            label = doc.get("label") or doc["filename"]
            logger.info(f"  ⬇️  [{doc['doc_type']}] {label}")

            # Открываем URL — Chrome скачает автоматически в DOCS_DIR
            driver.get(doc["url"])
            time.sleep(1.5)

            downloaded_path = _wait_download_complete(config.DOCS_DIR, timeout=30)

            if downloaded_path and downloaded_path.exists():
                # Определяем конечное имя файла
                # Если у скачанного файла нет нужного расширения — берём из doc
                dl_ext = downloaded_path.suffix.lower()
                if dl_ext in (".crdownload", ""):
                    dl_ext = doc.get("ext", "") or ".bin"

                filename = safe_filename(doc["filename"], doc.get("uid", ""), dl_ext)
                dest     = tender_dir / filename

                # Если файл с таким именем уже есть — добавляем суффикс
                counter = 1
                while dest.exists():
                    stem = Path(filename).stem
                    ext  = Path(filename).suffix
                    dest = tender_dir / f"{stem}_{counter}{ext}"
                    counter += 1

                shutil.move(str(downloaded_path), str(dest))
                file_size = dest.stat().st_size

                downloaded.append({
                    "filename":  dest.name,
                    "filepath":  str(dest),
                    "file_size": file_size,
                    "doc_type":  doc["doc_type"],
                })
                logger.info(f"  ✅ {dest.name} ({file_size // 1024} КБ)")
            else:
                logger.warning(f"  ⚠️  Не скачался: {label}")

            random_delay(1.0, 2.0)

        except Exception as e:
            logger.error(f"  Ошибка при скачивании [{doc.get('label')}]: {e}")
            continue

    return downloaded
