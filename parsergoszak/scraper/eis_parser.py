# ============================================================
#  scraper/eis_parser.py — парсинг страниц ЕИС
# ============================================================

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from loguru import logger

import config
from scraper.browser import safe_get, random_delay, wait_for


def build_search_url(keyword: str, page: int = 1) -> str:
    from urllib.parse import quote
    kw = quote(keyword, safe="")
    base = (
        f"{config.EIS_BASE}/epz/order/extendedsearch/results.html"
        f"?searchString={kw}"
        f"&morphology=on"
        f"&search-filter=Дате+размещения"
        f"&pageNumber={page}"
        f"&sortDirection={config.SORT_DIR}"
        f"&recordsPerPage=_{config.RECORDS_PER_PAGE}"
        f"&showLotsInfoHidden=false"
        f"&sortBy={config.SORT_BY}"
        f"&fz44=on&fz223=on&af=on&pc=on&pa=on"
    )
    return base


def _clean(text: str | None) -> str:
    return " ".join(text.split()) if text else ""


def _parse_price(text: str) -> float | None:
    if not text:
        return None
    digits = re.sub(r"[^\d,\.]", "", text).replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def parse_tender_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div.search-registry-entry-block")
    if not cards:
        cards = soup.select("div[class*='registry-entry']")

    results = []
    for card in cards:
        try:
            num_el = card.select_one("div.registry-entry__header-mid__number a")
            if not num_el:
                num_el = card.select_one("a[href*='purchaseNumber']")
            if not num_el:
                continue

            purchase_number = _clean(num_el.get_text())
            href = num_el.get("href", "")
            url = urljoin(config.EIS_BASE, href) if href else ""

            title_el = card.select_one("div.registry-entry__body-title")
            title = _clean(title_el.get_text()) if title_el else ""

            customer_el = card.select_one(
                "div.registry-entry__body-href a, div[class*='customer'] span"
            )
            customer = _clean(customer_el.get_text()) if customer_el else ""

            price_el = card.select_one("div.price-block__value, span[class*='price']")
            price_raw = _clean(price_el.get_text()) if price_el else ""
            price = _parse_price(price_raw)

            dates = card.select("div.data-block__value")
            publish_date = _clean(dates[0].get_text()) if len(dates) > 0 else ""
            deadline     = _clean(dates[1].get_text()) if len(dates) > 1 else ""

            law = "44-ФЗ" if "44-ФЗ" in card.get_text() else "223-ФЗ"

            status_el = card.select_one("div[class*='status']")
            status = _clean(status_el.get_text()) if status_el else ""

            results.append({
                "purchase_number": purchase_number,
                "title":           title,
                "customer":        customer,
                "law":             law,
                "price":           price,
                "currency":        "RUB",
                "publish_date":    publish_date,
                "deadline":        deadline,
                "status":          status,
                "url":             url,
                "raw_html":        str(card),
            })
        except Exception as e:
            logger.warning(f"Ошибка парсинга карточки: {e}")
            continue

    return results


def get_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    total_el = soup.select_one("span.search-results__total-count")
    if total_el:
        total_str = re.sub(r"\D", "", total_el.get_text())
        if total_str:
            total = int(total_str)
            return min((total // config.RECORDS_PER_PAGE) + 1, config.MAX_PAGES)

    page_items = soup.select("ul.pagination li a")
    if page_items:
        nums = []
        for a in page_items:
            t = re.sub(r"\D", "", a.get_text())
            if t.isdigit():
                nums.append(int(t))
        if nums:
            return min(max(nums), config.MAX_PAGES)
    return 1


def scrape_search_results(driver: webdriver.Chrome, keyword: str) -> list[dict]:
    all_tenders = []
    url_p1 = build_search_url(keyword, page=1)
    logger.info(f"🔍 Ищем: «{keyword}»")

    if not safe_get(driver, url_p1):
        logger.error("Не удалось открыть страницу поиска")
        return []

    wait_for(driver, By.CSS_SELECTOR,
             "div.search-registry-entry-block, div.no-result", timeout=20)

    html_p1  = driver.page_source
    tenders1 = parse_tender_cards(html_p1)
    total_pages = get_total_pages(html_p1)

    logger.info(f"  Стр. 1/{total_pages}: найдено {len(tenders1)} карточек")
    all_tenders.extend(tenders1)

    for page in range(2, total_pages + 1):
        random_delay()
        url = build_search_url(keyword, page=page)
        if not safe_get(driver, url):
            logger.warning(f"  Пропуск стр. {page}")
            continue
        wait_for(driver, By.CSS_SELECTOR, "div.search-registry-entry-block", timeout=20)
        html = driver.page_source
        tenders = parse_tender_cards(html)
        logger.info(f"  Стр. {page}/{total_pages}: найдено {len(tenders)} карточек")
        all_tenders.extend(tenders)

    logger.info(f"Итого по «{keyword}»: {len(all_tenders)} тендеров")
    return all_tenders
