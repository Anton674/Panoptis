# ============================================================
#  config.py — настройки тендерного агента (значения по умолчанию)
#
#  Обычно править этот файл не нужно: ключевые слова и основные
#  настройки задаются в settings.toml или параметрами запуска
#  (python main.py --help). Здесь — значения, которые используются,
#  если в settings.toml и в команде запуска параметр не указан.
# ============================================================

import os
from pathlib import Path

# --- Пути ---
BASE_DIR   = Path(__file__).parent
DOCS_DIR   = BASE_DIR / "docs"
LOGS_DIR   = BASE_DIR / "logs"
DB_PATH    = BASE_DIR / "storage" / "tenders.db"
SETTINGS_FILE = BASE_DIR / "settings.toml"

DOCS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(exist_ok=True)

# Имя флага готовности, который скрапер пишет по завершении скачивания
# документов закупки. ДОЛЖНО совпадать с DONE_FLAG_NAME в
# orchestrator/config.py — по нему watcher.py в оркестраторе понимает,
# что закупку можно ставить в очередь на анализ.
DONE_FLAG_NAME = "_done.flag"

# --- Ключевые слова поиска (замени под свою нишу) ---
SEARCH_KEYWORDS = [
    "поставка офисной бумаги",
    "поставка бумаги для принтера",
    "поставка бумаги а4",
]

# --- Фильтры ЕИС ---
LAWS = ["fz44"]                    # "fz44" и/или "fz223"
LAW_LABELS = {"fz44": "44-ФЗ", "fz223": "223-ФЗ"}   # как закон подписан в карточке/БД
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

# --- Скачивание ---
DOWNLOAD_LIMIT = 50                # макс. тендеров на скачивание за запуск

# --- Куда складывать документы ---
# "drive" — скачать и загрузить на Google Диск
# "local" — оставить только в DOCS_DIR на этом компьютере
TARGET = "drive"

# --- Google Диск ---
DRIVE_FOLDER_NAME      = "Panoptis — тендеры 44-ФЗ"   # папка в «Моём диске»
DRIVE_KEEP_LOCAL       = True      # False — удалять локальную копию после загрузки
DRIVE_CREDENTIALS_FILE = BASE_DIR / "credentials.json"   # OAuth-клиент из Google Cloud
DRIVE_TOKEN_FILE       = BASE_DIR / "token.json"         # создаётся после первого входа

# Маркер в папке закупки: всё из неё уже загружено на Google Диск
UPLOADED_FLAG_NAME = "_uploaded.flag"

# --- URL ЕИС ---
EIS_BASE      = "https://zakupki.gov.ru"
