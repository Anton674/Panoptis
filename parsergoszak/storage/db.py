# ============================================================
#  storage/db.py — хранение тендеров в SQLite
# ============================================================

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config import DB_PATH
from loguru import logger


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tenders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_number TEXT    UNIQUE NOT NULL,
                title           TEXT,
                customer        TEXT,
                law             TEXT,
                price           REAL,
                currency        TEXT DEFAULT 'RUB',
                publish_date    TEXT,
                deadline        TEXT,
                status          TEXT,
                url             TEXT,
                raw_html        TEXT,
                created_at      TEXT DEFAULT (datetime('now','localtime')),
                updated_at      TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                tender_id       INTEGER NOT NULL REFERENCES tenders(id),
                filename        TEXT,
                filepath        TEXT,
                file_size       INTEGER,
                doc_type        TEXT,
                downloaded_at   TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS search_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword     TEXT,
                pages_done  INTEGER,
                found_total INTEGER,
                new_saved   INTEGER,
                started_at  TEXT,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tenders_number ON tenders(purchase_number);
            CREATE INDEX IF NOT EXISTS idx_tenders_date   ON tenders(publish_date);
            CREATE INDEX IF NOT EXISTS idx_docs_tender    ON documents(tender_id);
        """)
    logger.info(f"БД инициализирована: {DB_PATH}")


def upsert_tender(data: dict) -> tuple[int, bool]:
    sql_insert = """
        INSERT OR IGNORE INTO tenders
            (purchase_number, title, customer, law, price, currency,
             publish_date, deadline, status, url, raw_html)
        VALUES
            (:purchase_number, :title, :customer, :law, :price, :currency,
             :publish_date, :deadline, :status, :url, :raw_html)
    """
    sql_update = """
        UPDATE tenders SET
            title        = :title,
            customer     = :customer,
            price        = :price,
            deadline     = :deadline,
            status       = :status,
            raw_html     = :raw_html,
            updated_at   = datetime('now','localtime')
        WHERE purchase_number = :purchase_number
    """
    with get_connection() as conn:
        cur = conn.execute(sql_insert, data)
        is_new = cur.rowcount > 0
        if not is_new:
            conn.execute(sql_update, data)
        row = conn.execute(
            "SELECT id FROM tenders WHERE purchase_number = ?",
            (data["purchase_number"],)
        ).fetchone()
        return row["id"], is_new


def save_document(tender_id: int, doc: dict) -> int:
    sql = """
        INSERT INTO documents (tender_id, filename, filepath, file_size, doc_type)
        VALUES (:tender_id, :filename, :filepath, :file_size, :doc_type)
    """
    doc["tender_id"] = tender_id
    with get_connection() as conn:
        cur = conn.execute(sql, doc)
        return cur.lastrowid


def log_search_run(run: dict) -> None:
    sql = """
        INSERT INTO search_runs (keyword, pages_done, found_total, new_saved, started_at, finished_at)
        VALUES (:keyword, :pages_done, :found_total, :new_saved, :started_at, :finished_at)
    """
    with get_connection() as conn:
        conn.execute(sql, run)


def is_already_downloaded(tender_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM documents WHERE tender_id = ?",
            (tender_id,)
        ).fetchone()
        return row["cnt"] > 0


def get_tenders_without_docs(limit: int = 100) -> list[dict]:
    sql = """
        SELECT t.* FROM tenders t
        LEFT JOIN documents d ON d.tender_id = t.id
        WHERE d.id IS NULL
        ORDER BY t.publish_date DESC
        LIMIT ?
    """
    with get_connection() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]
