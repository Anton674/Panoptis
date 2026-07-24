"""
watcher.py — следит за папкой DOCS_DIR и ставит закупки в очередь БД,
как только скрапер (downloader.py) записывает _done.flag в подпапку
конкретной закупки.

Работает через watchdog (события ФС в реальном времени) + разовое
сканирование при старте — на случай, если оркестратор был выключен,
пока скрапер уже успел что-то докачать.
"""

import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from loguru import logger

import config
import db


class DoneFlagHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).name != config.DONE_FLAG_NAME:
            return

        reg_number = Path(event.src_path).parent.name
        logger.info(f"🔔 Обнаружен флаг готовности: закупка {reg_number}")
        db.enqueue_purchase(reg_number)


def scan_existing_flags() -> None:
    """
    Разовое сканирование при старте: подхватывает закупки, чей
    _done.flag появился, пока оркестратор не работал.
    """
    if not config.DOCS_DIR.exists():
        logger.warning(f"DOCS_DIR не существует: {config.DOCS_DIR}")
        return

    count = 0
    for flag_path in config.DOCS_DIR.glob(f"*/{config.DONE_FLAG_NAME}"):
        reg_number = flag_path.parent.name
        if not db.is_known(reg_number):
            db.enqueue_purchase(reg_number)
            count += 1

    if count:
        logger.info(f"📋 При старте подхвачено закупок из существующих флагов: {count}")


def run_watcher() -> None:
    """Блокирующий цикл — запускать в отдельном потоке/процессе от воркера."""
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)

    scan_existing_flags()

    handler = DoneFlagHandler()
    observer = Observer()
    observer.schedule(handler, str(config.DOCS_DIR), recursive=True)
    observer.start()

    logger.info(f"👀 Watcher запущен, слежу за {config.DOCS_DIR}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    run_watcher()
