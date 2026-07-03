#!/usr/bin/env python3
# ============================================================
#  main.py — запуск тендерного агента
#
#  Использование:
#    python main.py                         # поиск по всем ключевым словам
#    python main.py --keyword "ИТ услуги"  # конкретный запрос
#    python main.py --only-download        # только скачать документы (уже найденных)
#    python main.py --no-download          # только собрать метаданные, без скачивания
# ============================================================

import sys
import argparse
from datetime import datetime
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
import config
from storage.db import init_db, upsert_tender, save_document, log_search_run, get_tenders_without_docs, is_already_downloaded
from scraper.browser import build_driver, random_delay
from scraper.eis_parser import scrape_search_results
from scraper.downloader import download_tender_docs


# ── Настройка логов ───────────────────────────────────────────────────────────

logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add(
    config.LOGS_DIR / "agent_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="1 day",
    retention="30 days",
    encoding="utf-8",
)


# ── Фазы работы ──────────────────────────────────────────────────────────────

def phase_search(driver, keywords: list[str]) -> list[dict]:
    """Фаза 1: поиск и сохранение метаданных тендеров."""
    logger.info("=" * 60)
    logger.info("ФАЗА 1 — Поиск тендеров")
    logger.info("=" * 60)

    all_results = []

    for keyword in keywords:
        started = datetime.now().isoformat()
        tenders = scrape_search_results(driver, keyword)

        new_count = 0
        for t in tenders:
            tender_id, is_new = upsert_tender(t)
            if is_new:
                new_count += 1

        log_search_run({
            "keyword":     keyword,
            "pages_done":  config.MAX_PAGES,
            "found_total": len(tenders),
            "new_saved":   new_count,
            "started_at":  started,
            "finished_at": datetime.now().isoformat(),
        })

        logger.info(f"✅ «{keyword}»: {len(tenders)} найдено, {new_count} новых")
        all_results.extend(tenders)
        random_delay(3.0, 6.0)   # пауза между ключевыми словами

    return all_results


def phase_download(driver, limit: int = 50) -> None:
    """Фаза 2: скачивание документов по ранее найденным тендерам."""
    logger.info("=" * 60)
    logger.info("ФАЗА 2 — Скачивание документов")
    logger.info("=" * 60)

    queue = get_tenders_without_docs(limit=limit)
    logger.info(f"В очереди на скачивание: {len(queue)} тендеров")

    for i, tender in enumerate(queue, 1):
        logger.info(f"[{i}/{len(queue)}] {tender['purchase_number']}")
        docs = download_tender_docs(driver, tender, tender["id"])
        for doc in docs:
            save_document(tender["id"], doc)
        random_delay(2.0, 4.0)

    logger.info(f"Фаза 2 завершена")


# ── Точка входа ───────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Тендерный агент ЕИС")
    p.add_argument("--keyword",       type=str, help="Конкретный поисковый запрос")
    p.add_argument("--no-download",   action="store_true", help="Только поиск, без скачивания")
    p.add_argument("--only-download", action="store_true", help="Только скачивание (пропустить поиск)")
    p.add_argument("--limit",         type=int, default=50, help="Макс. тендеров для скачивания")
    return p.parse_args()


def main():
    args = parse_args()

    # Инициализация БД
    init_db()

    # Ключевые слова
    keywords = [args.keyword] if args.keyword else config.SEARCH_KEYWORDS

    driver = build_driver()
    try:
        if not args.only_download:
            phase_search(driver, keywords)

        if not args.no_download:
            phase_download(driver, limit=args.limit)

    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
    finally:
        driver.quit()
        logger.info("Браузер закрыт. Работа завершена.")


if __name__ == "__main__":
    main()
