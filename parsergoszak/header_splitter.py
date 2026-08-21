"""
Модуль извлечения заголовков разделов через LLM (Qwen) и разбиения текста
документа на секции по этим заголовкам.

Предназначен для использования и как:
  - standalone-скрипт (test_header_extraction.py) — прогон на папке уже
    скачанных документов, без основного агента;
  - модуль, который потом импортируется в watcher.py/main.py на следующем
    этапе (передача секций в Aura для анализа).

Роли моделей (см. архитектуру Panoptis):
  - Qwen 7B (большой контекст, ~120k токенов) -> extract_headers():
    достаёт заголовки разделов дословно из ПОЛНОГО текста документа.
  - Aura 7B (текущая модель, GBNF-грамматика) -> анализирует уже нарезанные
    секции — это следующий этап пайплайна, не входит в этот модуль.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

import requests
from rapidfuzz import fuzz, utils as fuzz_utils

# Грамматика JSON-массива строк — заставляет Qwen корректно экранировать
# кавычки внутри заголовков (частый случай в тендерных текстах вида
# пункты "а"-"г") вместо того, чтобы полагаться на дисциплину промпта.
_HEADER_GRAMMAR = (Path(__file__).parent / "header_grammar.gbnf").read_text(encoding="utf-8")


class HeaderNotFoundError(ValueError):
    """Заголовок, который вернула LLM, не найден в исходном тексте документа."""

    def __init__(self, header: str, doc_name: str = ""):
        self.header = header
        self.doc_name = doc_name
        suffix = f" ({doc_name})" if doc_name else ""
        super().__init__(f"Заголовок не найден в тексте документа{suffix}: {header!r}")


@dataclass
class Section:
    header: str
    start: int
    end: int
    text: str


# Ориентировочный "словарь" типичных заголовков разделов — собран не из
# головы, а по факту прогона test_header_extraction.py на реальных
# документах закупок (см. header_test_output/). Нужен в первую очередь для
# документов БЕЗ явной роман-нумерации разделов (I., II., III. ...) —
# без визуальной разметки (текст извлекается без учёта жирности/стиля
# Word) Qwen не отличает заголовок раздела от обычного предложения и
# начинает путать целые предложения текста с заголовками (наблюдалось:
# 60 "заголовков" вместо ~13 реальных разделов в одном таком контракте).
# Это ориентир на уровень крупности и типовую лексику, а НЕ обязательный
# список для копирования — конкретные заголовки в документе могут
# называться иначе или не все присутствовать.
REFERENCE_SECTION_EXAMPLES = """\
Пример 1 — типичные заголовки разделов государственного/муниципального контракта:
- Предмет Контракта
- Цена Контракта и порядок расчетов
- Порядок, сроки и условия поставки и приемки Товара
- Взаимодействие Сторон
- Качество Товара
- Ответственность Сторон
- Обеспечение исполнения Контракта
- Исключительные права
- Обстоятельства непреодолимой силы
- Рассмотрение и разрешение споров
- Срок действия и порядок расторжения Контракта
- Прочие положения
- Перечень приложений
- Адреса и банковские реквизиты Сторон

Пример 2 — типичные заголовки разделов извещения об осуществлении закупки:
- Извещение об осуществлении закупки
- Описание объекта закупки
- Обоснование начальной (максимальной) цены контракта
- Требования к содержанию, составу заявки на участие в закупке и инструкция по её заполнению
- Проект контракта\
"""

HEADER_EXTRACTION_SYSTEM_PROMPT = (
    "Ты — помощник, который извлекает заголовки РАЗДЕЛОВ ВЕРХНЕГО УРОВНЯ из "
    "текста тендерного документа (документация о закупке по 44-ФЗ/223-ФЗ). "
    "Раздел верхнего уровня — это крупный смысловой блок документа, "
    "например «Раздел I. Общие положения», «Ответственность сторон», "
    "«Предмет договора», «Порядок расчётов». "
    "НЕ включай в ответ отдельные пронумерованные пункты и подпункты внутри "
    "раздела (например, «1.1.», «2.3.», «а)», «б)») — они входят в текст "
    "того раздела, к которому относятся, и не являются самостоятельными "
    "заголовками. Это касается и документов, где заголовки разделов НЕ "
    "выделены явной нумерацией (I., II., 1., 2. ...) — ориентируйся на "
    "смысл и типовую структуру таких документов (см. примеры ниже), а не "
    "только на визуальные маркеры нумерации.\n\n"
    "Для ориентира — примеры типичных заголовков разделов из похожих "
    "документов закупок. Это НЕ готовый список для копирования: "
    "конкретные заголовки твоего документа могут называться иначе или "
    "не все присутствовать. Смотри на них как на образец уровня "
    "крупности раздела и типовой лексики.\n\n"
    f"{REFERENCE_SECTION_EXAMPLES}\n\n"
    "Верни ТОЛЬКО JSON-массив строк, каждая строка — заголовок ДОСЛОВНО, "
    "точно так, как он написан в тексте (без изменения регистра, пробелов, "
    "нумерации). Порядок заголовков в массиве должен совпадать с порядком их "
    "появления в тексте. Не добавляй ничего кроме JSON-массива — никаких "
    "пояснений, никакого текста до или после."
)


def extract_headers(
    text: str,
    api_url: str = "http://localhost:8080/v1/chat/completions",
    model: str = "qwen",
    timeout: int = 300,
) -> List[str]:
    """
    Вызывает Qwen через OpenAI-совместимый endpoint llama.cpp
    (/v1/chat/completions — он сам применяет chat-шаблон instruct-модели,
    в отличие от /completion, под который пришлось бы вручную собирать
    ChatML-промпт конкретно под Qwen).

    Возвращает список заголовков в порядке появления в документе.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": HEADER_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "grammar": _HEADER_GRAMMAR,
    }
    resp = requests.post(api_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    raw_content = data["choices"][0]["message"]["content"]

    # На случай если модель обернула JSON в ```json ... ```
    cleaned = re.sub(r"^```(json)?|```$", "", raw_content.strip(), flags=re.MULTILINE).strip()

    try:
        headers = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Qwen вернул невалидный JSON вместо списка заголовков.\nСырой ответ:\n{raw_content}"
        ) from e

    if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
        raise ValueError(f"Ожидался JSON-массив строк, получено: {headers!r}")

    return headers


_WS_RE = re.compile(r"\s+")

# Насколько похожа найденная нечётким поиском строка должна быть на исходный
# заголовок (rapidfuzz.fuzz.ratio, 0-100), чтобы её принять. Подобрано с
# запасом — ниже этого порога чаще встречаются случайные совпадения, чем
# реальные перефразировки Qwen.
_FUZZY_MATCH_THRESHOLD = 85.0

# Насколько далеко вперёд от курсора искать нечёткое совпадение — реальный
# заголовок никогда не должен быть за горизонтом уже пройденного текста плюс
# разумный запас; ограничение нужно, чтобы не сканировать весь хвост
# документа посимвольно на каждый неточный заголовок.
_FUZZY_SEARCH_WINDOW_CHARS = 6000


def _whitespace_flexible_pattern(header: str) -> re.Pattern:
    """
    Собирает regex, где любая последовательность пробелов/переводов строк
    в заголовке заменена на `\\s+`. Прикрывает самый частый случай
    расхождения — Qwen местами схлопывает/меняет пробелы при дословном
    копировании длинных заголовков, хотя остальной текст совпадает точно.
    """
    parts = [p for p in _WS_RE.split(header.strip()) if p]
    # IGNORECASE — Qwen иногда нормализует регистр при копировании (напр.
    # исходное "ТЕХНИЧЕСКОЕ ЗАДАНИЕ" превращается в "Техническое задание"),
    # это ловится сразу здесь, а не только в дорогом нечётком поиске ниже.
    return re.compile(r"\s+".join(re.escape(p) for p in parts), re.IGNORECASE)


def _fuzzy_find(text: str, header: str, cursor: int) -> tuple[int, int] | None:
    """
    Последний резерв: скользящее окно + rapidfuzz.fuzz.ratio для случаев
    небольшой перефразировки (пунктуация, регистр), которые не ловит ни
    точный поиск, ни гибкий по пробелам. Возвращает (start, end) окна
    с наилучшим сходством, если оно не ниже _FUZZY_MATCH_THRESHOLD.
    """
    window = len(header)
    if window == 0:
        return None

    region_end = min(len(text), cursor + _FUZZY_SEARCH_WINDOW_CHARS)
    region = text[cursor:region_end]
    step = max(1, window // 4)

    best_score = 0.0
    best_pos = -1
    for start in range(0, max(1, len(region) - window + 1), step):
        candidate = region[start:start + window]
        # processor=default_process — регистронезависимо, как и regex-ступень
        # выше, плюс схлопывает пробелы/пунктуацию по краям.
        score = fuzz.ratio(candidate, header, processor=fuzz_utils.default_process)
        if score > best_score:
            best_score, best_pos = score, start

    if best_pos == -1 or best_score < _FUZZY_MATCH_THRESHOLD:
        return None
    abs_start = cursor + best_pos
    return abs_start, abs_start + window


def _locate_header(text: str, header: str, cursor: int, doc_name: str) -> tuple[int, int]:
    """Пробует точный поиск -> гибкий по пробелам -> нечёткий, в этом порядке."""
    idx = text.find(header, cursor)
    if idx != -1:
        return idx, idx + len(header)

    m = _whitespace_flexible_pattern(header).search(text, cursor)
    if m is not None:
        return m.start(), m.end()

    fuzzy = _fuzzy_find(text, header, cursor)
    if fuzzy is not None:
        return fuzzy

    raise HeaderNotFoundError(header, doc_name)


def split_by_headers(text: str, headers: List[str], doc_name: str = "") -> List[Section]:
    """
    Режет текст документа на секции по заголовкам, возвращённым Qwen.

    Поиск позиции каждого заголовка идёт по трём уровням (см. _locate_header):
    точное вхождение -> вхождение с гибкими пробелами -> нечёткое совпадение
    (rapidfuzz), продолжая вперёд от конца предыдущего найденного заголовка.

    Модель почти всегда возвращает заголовки в порядке их появления в
    тексте (как и просит промпт), но изредка — заметно на канонических
    шаблонных документах (типовой контракт) — переставляет местами один
    из последних заголовков ближе к порядку, который "ожидает" по своим
    знаниям о типовой структуре, а не по факту документа (например,
    "Адреса и реквизиты Сторон" перед "Приложение № 1", хотя в тексте
    наоборот). Если поиск вперёд от курсора не находит заголовок ни одним
    из трёх способов, повторяем поиск с самого начала текста — и в конце
    сортируем секции по фактической позиции в тексте, а не по порядку
    заголовков в исходном списке.

    Секция = "от найденного заголовка до начала следующего заголовка",
    последняя секция — до конца документа. Текст ДО первого найденного
    заголовка (титульный лист, шапка) в секции не попадает.
    """
    if not headers:
        raise ValueError("Пустой список заголовков — нечего резать.")

    positions = []
    cursor = 0
    for header in headers:
        try:
            start, end = _locate_header(text, header, cursor, doc_name)
        except HeaderNotFoundError:
            start, end = _locate_header(text, header, 0, doc_name)
        positions.append((header, start, end))
        cursor = max(cursor, end)

    positions.sort(key=lambda p: p[1])

    sections = []
    for i, (header, start, _end) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        sections.append(Section(header=header, start=start, end=end, text=text[start:end]))

    return sections
