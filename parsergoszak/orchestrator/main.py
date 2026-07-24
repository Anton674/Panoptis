"""
main.py — воркер оркестратора Panoptis.

Логика:
  1. Раз в POLL_INTERVAL_SEC секунд берёт из БД одну закупку в статусе
     'pending' (её туда кладёт watcher.py, работающий отдельным процессом).
  2. Читает _done.flag в папке закупки, чтобы получить список файлов
     и их doc_type (проставленный скрапером).
  3. Для каждого файла: извлекает текст → отправляет в LLM → парсит JSON →
     пишет найденные позиции в purchase_items, ошибки — в processing_log.
  4. По завершении всех файлов закупки — статус 'done'.

Запускать watcher.py и main.py как два отдельных процесса
(например, два systemd-юнита / две задачи Планировщика Windows,
или два PowerShell-окна на время разработки).
"""

import json
import time
from pathlib import Path

from loguru import logger

import config
import db
import llm_client
import text_extractor


def _load_flag_metadata(purchase_dir: Path) -> dict:
    """Читает _done.flag и возвращает {filename: doc_type}."""
    flag_path = purchase_dir / config.DONE_FLAG_NAME
    if not flag_path.exists():
        logger.warning(f"  Флаг не найден в {purchase_dir} — обрабатываю без doc_type")
        return {}

    try:
        payload = json.loads(flag_path.read_text(encoding="utf-8"))
        return {f["filename"]: f.get("doc_type") for f in payload.get("files", [])}
    except Exception as e:
        logger.error(f"  Не удалось прочитать {flag_path}: {e}")
        return {}


def process_purchase(reg_number: str) -> None:
    purchase_dir = config.DOCS_DIR / reg_number
    logger.info(f"⚙️  Обрабатываю закупку {reg_number}")

    if not purchase_dir.exists():
        logger.error(f"  Папка закупки не найдена: {purchase_dir}")
        db.set_status(reg_number, "error")
        return

    doc_type_map = _load_flag_metadata(purchase_dir)

    files = [
        f for f in purchase_dir.iterdir()
        if f.is_file() and f.name != config.DONE_FLAG_NAME
    ]

    if not files:
        logger.info(f"  В закупке {reg_number} нет файлов для анализа")
        db.set_status(reg_number, "done")
        return

    for file_path in files:
        _process_file(reg_number, file_path, doc_type_map.get(file_path.name))

    db.set_status(reg_number, "done")
    logger.info(f"✅ Закупка {reg_number} обработана")


def _process_file(reg_number: str, file_path: Path, doc_type: str | None) -> None:
    source_file = file_path.name

    if file_path.suffix.lower() not in config.SUPPORTED_EXTENSIONS:
        logger.info(f"  ⏭️  {source_file}: формат не поддержан, пропускаю")
        db.insert_log(reg_number, source_file, "unsupported_ext")
        return

    text = text_extractor.extract_text(file_path)
    if not text or not text.strip():
        logger.info(f"  ⏭️  {source_file}: пустой текст после извлечения")
        db.insert_log(reg_number, source_file, "empty")
        return

    try:
        result = llm_client.analyze_document(text, source_file)
    except llm_client.LLMError as e:
        logger.error(f"  ❌ {source_file}: {e}")
        db.insert_log(reg_number, source_file, "llm_error", str(e))
        return

    raw_response = result.get("_raw", "")

    if not result.get("found"):
        logger.info(f"  ◻️  {source_file}: объект закупки не найден")
        db.insert_log(reg_number, source_file, "ok")
        return

    items = result.get("items", [])
    for item in items:
        db.insert_item(
            reg_number=reg_number,
            source_file=source_file,
            doc_type=doc_type,
            item_name=item.get("name", ""),
            quantity=item.get("quantity"),
            unit=item.get("unit"),
            raw_llm_response=raw_response,
        )

    logger.info(f"  ✅ {source_file}: найдено позиций — {len(items)}")
    db.insert_log(reg_number, source_file, "ok")


def run_worker() -> None:
    db.init_db()
    logger.info("🚀 Воркер оркестратора запущен")

    while True:
        reg_number = db.get_next_pending()

        if reg_number is None:
            time.sleep(config.POLL_INTERVAL_SEC)
            continue

        db.set_status(reg_number, "processing")
        try:
            process_purchase(reg_number)
        except Exception as e:
            logger.exception(f"Необработанная ошибка при обработке {reg_number}: {e}")
            db.set_status(reg_number, "error")


if __name__ == "__main__":
    run_worker()
