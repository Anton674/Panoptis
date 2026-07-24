# ============================================================
#  scraper/browser.py — Selenium с умными ретраями
#  вместо жёстких таймаутов — повторные попытки при обрыве
# ============================================================

import time
import random
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from loguru import logger

import config


def build_driver(download_dir: Path = config.DOCS_DIR) -> webdriver.Chrome:
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-plugins-discovery")
    options.add_argument("--disable-pdf-viewer")
    options.add_argument("--dns-prefetch-disable")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
    options.add_argument(f"--user-agent={ua}")

    prefs = {
        "download.default_directory":        str(download_dir.resolve()),
        "download.prompt_for_download":      False,
        "download.directory_upgrade":        True,
        "safebrowsing.enabled":              True,
        "plugins.always_open_pdf_externally":           True,
        "download.extensions_to_open":                  "",
        "profile.default_content_settings.popups":      0,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "plugins.plugins_disabled": ["Chrome PDF Viewer", "Chrome PDF Plugin"],
    }
    options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """
    })

    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(config.IMPLICIT_WAIT)
    logger.info("Браузер запущен")
    return driver


def random_delay(min_s: float = config.MIN_DELAY,
                 max_s: float = config.MAX_DELAY) -> None:
    time.sleep(random.uniform(min_s, max_s))


def wait_for_connection(driver: webdriver.Chrome,
                        max_wait: int = 120) -> bool:
    """
    Ждёт восстановления интернета.
    Проверяет каждые 5 секунд, максимум max_wait секунд.
    """
    deadline = time.time() + max_wait
    attempt  = 0
    while time.time() < deadline:
        attempt += 1
        try:
            result = driver.execute_script(
                "var xhr = new XMLHttpRequest();"
                "xhr.open('HEAD', 'https://zakupki.gov.ru/favicon.ico', false);"
                "try { xhr.send(); return xhr.status > 0; }"
                "catch(e) { return false; }"
            )
            if result:
                logger.info(f"  ✅ Соединение восстановлено (попытка {attempt})")
                return True
        except Exception:
            pass
        remaining = int(deadline - time.time())
        logger.warning(
            f"  📡 Нет соединения, жду 5 сек... "
            f"(попытка {attempt}, осталось {remaining} сек)"
        )
        time.sleep(5)
    return False


def _page_is_loaded(driver: webdriver.Chrome) -> bool:
    """Страница не пустая и полностью загружена."""
    try:
        state = driver.execute_script("return document.readyState")
        body  = driver.execute_script(
            "return document.body ? document.body.innerText.length : 0"
        )
        return state == "complete" and body > 100
    except Exception:
        return False


def _short_error(msg: str) -> str:
    lines = [l.strip() for l in msg.strip().splitlines() if l.strip()]
    return lines[0][:120] if lines else msg[:120]


def safe_get(driver: webdriver.Chrome, url: str,
             retries: int = 5) -> bool:
    """
    Открывает URL с умными ретраями.
    При сетевых ошибках ждёт восстановления соединения.
    При таймауте останавливает загрузку и пробует снова.
    """
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)

            if _page_is_loaded(driver):
                if attempt > 1:
                    logger.info(f"  ✅ Загружено со {attempt}-й попытки")
                return True
            else:
                logger.warning(
                    f"  ⚠️  Страница пустая или не загрузилась "
                    f"(попытка {attempt}/{retries})"
                )

        except TimeoutException:
            logger.warning(
                f"  ⏱️  Таймаут загрузки (попытка {attempt}/{retries}) — "
                f"останавливаю и пробую снова"
            )
            try:
                driver.execute_script("window.stop();")
                if _page_is_loaded(driver):
                    logger.info("  ✅ Страница частично загрузилась — продолжаем")
                    return True
            except Exception:
                pass

        except WebDriverException as e:
            err_str = str(e).lower()
            is_network = any(k in err_str for k in [
                "net::", "connection", "network", "ssl",
                "certificate", "err_name_not_resolved",
                "err_internet_disconnected", "err_connection_refused"
            ])

            if is_network:
                logger.warning(
                    f"  📡 Сетевая ошибка (попытка {attempt}/{retries}): "
                    f"{_short_error(str(e))}"
                )
                logger.info("  ⏳ Жду восстановления интернета (макс. 2 мин)...")
                if not wait_for_connection(driver, max_wait=120):
                    logger.error("  ❌ Интернет не появился за 2 минуты, пропускаю")
                    return False
                continue
            else:
                logger.error(
                    f"  ❌ Ошибка браузера (попытка {attempt}/{retries}): "
                    f"{_short_error(str(e))}"
                )

        wait = min(5 * attempt, 30)
        if attempt < retries:
            logger.info(f"  🔄 Повтор через {wait} сек...")
            time.sleep(wait)

    logger.error(f"  ❌ Не удалось загрузить после {retries} попыток: {url}")
    return False


def wait_for(driver: webdriver.Chrome,
             by: str, selector: str,
             timeout: int = 15) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )
        return True
    except TimeoutException:
        return False
