"""
text_extractor.py — извлечение текста из документов закупки.

MVP: .docx и .xlsx. Порядок важен — используем iter_block_items для docx,
чтобы параграфы и таблицы шли в исходном порядке документа, а не
таблицы отдельно от текста (как при доступе через .paragraphs / .tables).
"""

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from loguru import logger

import config


# ── DOCX ──────────────────────────────────────────────────────────────────

def _iter_block_items(document: Document):
    """
    Итерирует параграфы и таблицы документа в порядке их появления.
    document.paragraphs и document.tables по отдельности теряют порядок —
    iter_block_items идёт напрямую по XML-дереву тела документа.
    """
    parent_elm = document.element.body
    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _table_to_text(table: Table) -> str:
    """Простое построчное представление таблицы (без markdown-выравнивания —
    для LLM важнее сохранить содержимое, а не визуальную ровность)."""
    lines = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_docx_text(path: Path) -> str:
    document = Document(str(path))
    parts = []
    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                parts.append(text)
        elif isinstance(block, Table):
            table_text = _table_to_text(block)
            if table_text.strip():
                parts.append(table_text)
    return "\n".join(parts)


# ── XLSX ──────────────────────────────────────────────────────────────────

def extract_xlsx_text(path: Path) -> str:
    """
    Конвертирует все листы книги в простые markdown-таблицы.
    Объединённые ячейки (merged cells) разворачиваются: значение
    верхней левой ячейки диапазона копируется во все ячейки диапазона,
    чтобы модель не видела пустых полей там, где по смыслу есть значение.
    """
    wb = load_workbook(str(path), data_only=True)
    parts = []

    for sheet in wb.worksheets:
        merged_value_map = {}
        for merged_range in sheet.merged_cells.ranges:
            top_left = merged_range.min_row, merged_range.min_col
            value = sheet.cell(row=top_left[0], column=top_left[1]).value
            for row in range(merged_range.min_row, merged_range.max_row + 1):
                for col in range(merged_range.min_col, merged_range.max_col + 1):
                    merged_value_map[(row, col)] = value

        rows_text = []
        for row in sheet.iter_rows():
            cells = []
            for cell in row:
                value = merged_value_map.get((cell.row, cell.column), cell.value)
                cells.append("" if value is None else str(value).strip())
            if any(cells):
                rows_text.append(" | ".join(cells))

        if rows_text:
            parts.append(f"### Лист: {sheet.title}\n" + "\n".join(rows_text))

    return "\n\n".join(parts)


# ── Диспетчер ─────────────────────────────────────────────────────────────

def extract_text(path: Path) -> str | None:
    """
    Возвращает извлечённый текст документа, либо None, если формат
    не поддерживается (см. config.SUPPORTED_EXTENSIONS).
    """
    ext = path.suffix.lower()

    if ext not in config.SUPPORTED_EXTENSIONS:
        logger.warning(f"  Формат {ext} пока не поддержан для {path.name}")
        return None

    try:
        if ext == ".docx":
            text = extract_docx_text(path)
        elif ext == ".xlsx":
            text = extract_xlsx_text(path)
        else:
            return None
    except Exception as e:
        logger.error(f"  Ошибка извлечения текста из {path.name}: {e}")
        return None

    if len(text) > config.MAX_DOC_CHARS:
        logger.warning(
            f"  {path.name}: текст обрезан с {len(text)} до "
            f"{config.MAX_DOC_CHARS} символов (см. config.MAX_DOC_CHARS)"
        )
        text = text[:config.MAX_DOC_CHARS]

    return text
