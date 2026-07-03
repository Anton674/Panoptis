# ============================================================
#  scraper/browser.py — Selenium-браузер с антибот-настройками
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
    """
    Создаёт Chrome WebDriver с настройками:
    - скачивание в нужную папку без диалога
    - рандомный user-agent
    - базовые антидетект-параметры
    """
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
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
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled":              True,
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
    t = random.uniform(min_s, max_s)
    time.sleep(t)


def safe_get(driver: webdriver.Chrome, url: str, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            random_delay(1.0, 2.5)
            return True
        except TimeoutException:
            logger.warning(f"Таймаут загрузки (попытка {attempt}/{retries}): {url}")
        except WebDriverException as e:
            logger.error(f"WebDriver ошибка (попытка {attempt}/{retries}): {e}")
        time.sleep(attempt * 3)
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
