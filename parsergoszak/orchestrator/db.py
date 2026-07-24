"""
db.py — слой хранения оркестратора Panoptis (SQLite).

Схема:
  purchases       — одна строка на закупку, статус для очереди обработки
  purchase_items  — сырые находки LLM: позиция + файл-источник + тип документа
  processing_log  — журнал обработки каждого файла (успех/ошибка), для отладки

Дедупликация позиций между документами одной закупки НЕ делается здесь —
это отдельный шаг поверх purchase_items (например, через rapidfuzz),
когда накопится достаточно реальных данных, чтобы понять паттерны
расхождений в формулировках.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    reg_number TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending / processing / done / error
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    reg_number       TEXT NOT NULL REFERENCES purchases(reg_number),
    source_file      TEXT NOT NULL,   -- имя файла-источника, напр. "Проект_контракта.docx"
    doc_type         TEXT,            -- "ТЗ" / "проект_контракта" / "прочее" и т.д. (из флага скрапера)
    item_name        TEXT NOT NULL,
    quantity         REAL,
    unit             TEXT,
    raw_llm_response TEXT,            -- полный сырой JSON-ответ модели (для отладки/переобучения)
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reg_number    TEXT NOT NULL,
    source_file   TEXT NOT NULL,
    status        TEXT NOT NULL,      -- ok / not_found / invalid_json / llm_error / unsupported_ext
    error_message TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_purchases_status ON purchases(status);
CREATE INDEX IF NOT EXISTS idx_items_reg_number ON purchase_items(reg_number);
CREATE INDEX IF NOT EXISTS idx_log_reg_number ON processing_log(reg_number);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def get_conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ── Очередь закупок ─────────────────────────────────────────────────────

def enqueue_purchase(reg_number: str) -> None:
    """Добавляет закупку в очередь, если её там ещё нет (идемпотентно)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO purchases (reg_number, status, created_at, updated_at)
            VALUES (?, 'pending', ?, ?)
            ON CONFLICT(reg_number) DO NOTHING
            """,
            (reg_number, _now(), _now()),
        )


def get_next_pending() -> str | None:
    """Возвращает reg_number следующей закупки в статусе pending, либо None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT reg_number FROM purchases WHERE status = 'pending' "
            "ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        return row["reg_number"] if row else None


def set_status(reg_number: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE purchases SET status = ?, updated_at = ? WHERE reg_number = ?",
            (status, _now(), reg_number),
        )


def is_known(reg_number: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM purchases WHERE reg_number = ?", (reg_number,)
        ).fetchone()
        return row is not None


# ── Результаты анализа ───────────────────────────────────────────────────

def insert_item(reg_number: str, source_file: str, doc_type: str | None,
                 item_name: str, quantity: float | None, unit: str | None,
                 raw_llm_response: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO purchase_items
                (reg_number, source_file, doc_type, item_name, quantity, unit,
                 raw_llm_response, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (reg_number, source_file, doc_type, item_name, quantity, unit,
             raw_llm_response, _now()),
        )


def insert_log(reg_number: str, source_file: str, status: str,
               error_message: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO processing_log
                (reg_number, source_file, status, error_message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (reg_number, source_file, status, error_message, _now()),
        )
