# ============================================================
#  config.py — настройки тендерного агента
# ============================================================

import os
from pathlib import Path

# --- Пути ---
BASE_DIR   = Path(__file__).parent
DOCS_DIR   = BASE_DIR / "docs"
LOGS_DIR   = BASE_DIR / "logs"
DB_PATH    = BASE_DIR / "storage" / "tenders.db"

DOCS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(exist_ok=True)

# --- Ключевые слова поиска (замени под свою нишу) ---
SEARCH_KEYWORDS = [
    "разработка программного обеспечения",
    "информационная система",
    "техническое обслуживание",
]

# --- Фильтры ЕИС ---
LAWS = ["fz44", "fz223"]          # 44-ФЗ и/или 223-ФЗ
RECORDS_PER_PAGE = 50              # 10 / 20 / 50
MAX_PAGES        = 5               # сколько страниц пагинации обходить
SORT_BY          = "UPDATE_DATE"   # PUBLISH_DATE / UPDATE_DATE / PRICE
SORT_DIR         = "false"         # false = по убыванию (новые первые)

# --- Selenium ---
HEADLESS         = True            # False — смотреть браузер живьём
PAGE_LOAD_TIMEOUT = 30             # секунды
IMPLICIT_WAIT     = 10
DOWNLOAD_TIMEOUT  = 60             # таймаут скачивания документа

# --- Антибот ---
MIN_DELAY = 2.0                    # минимальная пауза между запросами (сек)
MAX_DELAY = 5.0                    # максимальная пауза

# --- URL ЕИС ---
EIS_BASE      = "https://zakupki.gov.ru"
EIS_SEARCH    = (
    f"{EIS_BASE}/epz/order/extendedsearch/results.html"
    "?morphology=on"
    "&search-filter=Дате+размещения"
    f"&recordsPerPage=_{RECORDS_PER_PAGE}"
    "&showLotsInfoHidden=false"
    f"&sortBy={SORT_BY}"
    f"&sortDirection={SORT_DIR}"
    "&fz44=on"
    "&fz223=on"
    "&af=on&pc=on&pa=on"
)
