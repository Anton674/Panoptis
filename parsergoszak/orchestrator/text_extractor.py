"""
text_extractor.py — извлечение текста из документов закупки.

Поддержаны: .docx, .xlsx, .rtf, .odt, .doc.
Порядок важен для .docx — используем iter_block_items, чтобы параграфы
и таблицы шли в исходном порядке документа, а не таблицы отдельно от
текста (как при доступе через .paragraphs / .tables).

.doc (старый бинарный формат Word 97-2003) в чистом Python нормально
не парсится — конвертируем через LibreOffice (soffice --headless).
Если LibreOffice не установлен, .doc пропускается с понятным
предупреждением в лог, остальные форматы это не затрагивает.
"""

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from striprtf.striprtf import rtf_to_text
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


# ── RTF ───────────────────────────────────────────────────────────────────

def extract_rtf_text(path: Path) -> str:
    # RTF-контейнер обычно чистый ASCII (не-ASCII символы закодированы
    # управляющими последовательностями \'XX / \uNNNN внутри самого
    # RTF), но кодировка исходного файла на диске всё равно может
    # отличаться — пробуем utf-8, затем cp1251 (частый вариант для
    # старых русских документов).
    raw_bytes = path.read_bytes()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw = raw_bytes.decode("cp1251", errors="ignore")
    return rtf_to_text(raw)


# ── ODT ───────────────────────────────────────────────────────────────────

_ODT_TEXT_TAGS = {
    "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p",
    "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}h",
}


def extract_odt_text(path: Path) -> str:
    """
    ODT — это zip-архив с content.xml внутри. Разворачивать через
    сторонние библиотеки (odfpy) избыточно для MVP — просто читаем
    content.xml и вытаскиваем текст параграфов/заголовков напрямую.
    """
    with zipfile.ZipFile(path) as zf:
        xml_bytes = zf.read("content.xml")

    root = ET.fromstring(xml_bytes)
    parts = []
    for elem in root.iter():
        if elem.tag in _ODT_TEXT_TAGS:
            text = "".join(elem.itertext()).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


# ── DOC (старый бинарный формат) ────────────────────────────────────────

def extract_doc_text(path: Path) -> str:
    """
    .doc в чистом Python нормально не читается — нет надёжной
    библиотеки под это (в отличие от .docx/.odt, это не XML/zip,
    а бинарный OLE-формат). Конвертируем через установленный
    LibreOffice (soffice --headless --convert-to txt).

    Если LibreOffice не найден в PATH — бросаем понятную ошибку,
    её ловит extract_text() и файл просто помечается как
    неизвлечённый (без падения всего пайплайна).
    """
    soffice = shutil.which("soffice") or shutil.which("soffice.exe")
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) не найден в PATH — нужен для чтения .doc. "
            "Установи LibreOffice (бесплатно, https://ru.libreoffice.org/) "
            "и убедись, что soffice.exe добавлен в PATH."
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", tmp_dir, str(path)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"soffice завершился с ошибкой: {result.stderr.decode(errors='ignore')}")

        txt_path = Path(tmp_dir) / (path.stem + ".txt")
        if not txt_path.exists():
            raise RuntimeError(f"soffice не создал ожидаемый файл {txt_path}")

        return txt_path.read_text(encoding="utf-8", errors="ignore")


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
        elif ext == ".rtf":
            text = extract_rtf_text(path)
        elif ext == ".odt":
            text = extract_odt_text(path)
        elif ext == ".doc":
            text = extract_doc_text(path)
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
