#!/usr/bin/env python3
# ============================================================
#  main.py — запуск тендерного агента
#
#  Использование:
#    python main.py                          # всё по settings.toml
#    python main.py --drive                  # скачать и загрузить на Google Диск
#    python main.py --local                  # скачать только на этот компьютер
#    python main.py -k "ИТ услуги" -k "ПО"   # свои запросы вместо settings.toml
#    python main.py --config другой.toml     # другой файл настроек
#    python main.py --only-download          # только скачать документы (уже найденных)
#    python main.py --no-download            # только собрать метаданные, без скачивания
#    python main.py --only-upload            # только догрузить на Диск уже скачанное
#    python main.py --drive-login            # войти в Google (один раз) и проверить Диск
#    python main.py --help                   # все параметры
# ============================================================

import sys
import argparse
from datetime import datetime
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
import config
import settings
from storage.db import init_db, upsert_tender, save_document, log_search_run, get_tenders_without_docs, get_tender_laws
from scraper.browser import build_driver, random_delay
from scraper.eis_parser import scrape_search_results
from scraper.downloader import download_tender_docs, tender_dir_for


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


def phase_download(driver, limit: int = 50, uploader=None) -> None:
    """Фаза 2: скачивание документов по ранее найденным тендерам."""
    logger.info("=" * 60)
    logger.info("ФАЗА 2 — Скачивание документов")
    logger.info("=" * 60)

    queue = get_tenders_without_docs(limit=limit, laws=_law_labels())
    logger.info(f"В очереди на скачивание: {len(queue)} тендеров")

    for i, tender in enumerate(queue, 1):
        logger.info(f"[{i}/{len(queue)}] {tender['purchase_number']}")
        docs = download_tender_docs(driver, tender, tender["id"])
        for doc in docs:
            save_document(tender["id"], doc)

        tender_dir = tender_dir_for(tender, tender["id"])
        if uploader and (tender_dir / config.DONE_FLAG_NAME).exists():
            uploader.upload_tender(tender_dir)
        random_delay(2.0, 4.0)

    logger.info(f"Фаза 2 завершена")


def phase_upload(uploader) -> None:
    """Фаза 3: догрузить на Google Диск то, что скачано, но ещё не загружено."""
    allowed = set(_law_labels())
    law_by_dir = {tender_dir_for(t, t["id"]).name: t["law"] for t in get_tender_laws()}

    pending = [
        d for d in sorted(config.DOCS_DIR.iterdir())
        if d.is_dir()
        and (d / config.DONE_FLAG_NAME).exists()
        and not (d / config.UPLOADED_FLAG_NAME).exists()
        # папки закупок по невыбранному закону (например, старые 223-ФЗ) не трогаем
        and law_by_dir.get(d.name) in allowed | {None}
    ]
    if not pending:
        logger.info("☁️  Всё скачанное уже на Google Диске")
        return

    logger.info("=" * 60)
    logger.info(f"ФАЗА 3 — Загрузка на Google Диск (закупок: {len(pending)})")
    logger.info("=" * 60)
    failed = sum(not uploader.upload_tender(d) for d in pending)
    if failed:
        logger.warning(f"Не загрузилось закупок: {failed} — попробую при следующем запуске")


def _law_labels() -> list[str]:
    return [config.LAW_LABELS[law] for law in config.LAWS]


# ── Точка входа ───────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Тендерный агент ЕИС. Параметры запуска важнее значений из settings.toml.")

    where = p.add_mutually_exclusive_group()
    where.add_argument("--drive", dest="target", action="store_const", const="drive",
                       help="Скачать и загрузить на Google Диск")
    where.add_argument("--local", dest="target", action="store_const", const="local",
                       help="Скачать только на этот компьютер")

    p.add_argument("-k", "--keyword", dest="keywords", action="append", metavar="ЗАПРОС",
                   help="Поисковый запрос; можно несколько раз: -k \"бумага\" -k \"картридж\"")
    p.add_argument("--config", type=Path, metavar="ФАЙЛ",
                   help="Файл настроек (по умолчанию settings.toml рядом с main.py)")
    p.add_argument("--laws", nargs="+", metavar="ЗАКОН", help="44 и/или 223")
    p.add_argument("--pages", dest="max_pages", type=int, metavar="N",
                   help="Сколько страниц результатов обходить на каждый запрос")
    p.add_argument("--limit", dest="download_limit", type=int, metavar="N",
                   help="Макс. тендеров для скачивания")
    p.add_argument("--docs-dir", type=Path, metavar="ПАПКА", help="Куда качать документы")
    p.add_argument("--drive-folder", metavar="ИМЯ", help="Папка на Google Диске")
    p.add_argument("--show-browser", dest="headless", action="store_false",
                   help="Показывать окно браузера")
    keep = p.add_mutually_exclusive_group()
    keep.add_argument("--keep-local", dest="keep_local_copy", action="store_true",
                      help="Оставлять файлы на компьютере после загрузки на Диск")
    keep.add_argument("--no-keep-local", dest="keep_local_copy", action="store_false",
                      help="Удалять файлы с компьютера после загрузки на Диск")

    p.add_argument("--no-download",   action="store_true", help="Только поиск, без скачивания")
    p.add_argument("--only-download", action="store_true", help="Только скачивание (пропустить поиск)")
    p.add_argument("--only-upload",   action="store_true",
                   help="Только догрузить на Google Диск уже скачанное (без браузера)")
    p.add_argument("--drive-login",   action="store_true",
                   help="Войти в Google и проверить доступ к Диску, без поиска")

    # None = «не указано в команде» → берётся значение из settings.toml
    p.set_defaults(target=None, headless=None, keep_local_copy=None)
    return p.parse_args()


def main():
    args = parse_args()

    try:
        settings.load(args.config and args.config.resolve(), {
            "target":           args.target,
            "keywords":         args.keywords,
            "laws":             args.laws,
            "max_pages":        args.max_pages,
            "download_limit":   args.download_limit,
            "docs_dir":         args.docs_dir and args.docs_dir.resolve(),
            "drive_folder":     args.drive_folder,
            "headless":         args.headless,
            "keep_local_copy":  args.keep_local_copy,
        })
    except settings.SettingsError as e:
        logger.error(f"Ошибка настроек — {e}")
        sys.exit(2)

    if args.only_upload or args.drive_login:
        config.TARGET = "drive"

    logger.info(
        f"Куда: {'Google Диск' if config.TARGET == 'drive' else 'только этот компьютер'} · "
        f"законы: {', '.join(_law_labels())} · папка загрузок: {config.DOCS_DIR}"
    )

    # Инициализация БД
    init_db()

    uploader = None
    if config.TARGET == "drive" and (not args.no_download or args.drive_login):
        # google-библиотеки нужны только для Диска — с --local без них тоже работает
        from storage.drive import DriveUploader, DriveError
        uploader = DriveUploader()
        try:
            uploader.connect()
        except Exception as e:
            if isinstance(e, DriveError) or args.drive_login or args.only_upload:
                logger.error(f"Google Диск недоступен: {e}")
                logger.info("Чтобы качать только на этот компьютер, запусти с --local")
                sys.exit(1)
            # Сеть/Google временно недоступны — качаем на компьютер, загрузим в следующий раз
            logger.warning(f"Google Диск сейчас недоступен ({e}) — качаю на компьютер, "
                           f"загружу на Диск при следующем запуске")
            uploader = None

    if args.drive_login:
        logger.info("Доступ к Google Диску есть — можно запускать парсер")
        return

    try:
        if args.only_upload:
            phase_upload(uploader)
            return

        driver = build_driver()
        finished = False
        try:
            if not args.only_download:
                phase_search(driver, config.SEARCH_KEYWORDS)

            if not args.no_download:
                phase_download(driver, limit=config.DOWNLOAD_LIMIT, uploader=uploader)
            finished = True

        except KeyboardInterrupt:
            logger.info("Остановлено пользователем")
        except Exception as e:
            logger.exception(f"Критическая ошибка: {e}")
        finally:
            driver.quit()
            logger.info("Браузер закрыт.")

        # Догружаем то, что не ушло на Диск в этот или прошлые запуски
        if uploader and finished:
            phase_upload(uploader)

    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")

    logger.info("Работа завершена.")


if __name__ == "__main__":
    main()
