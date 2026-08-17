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
from typing import List

import requests


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


HEADER_EXTRACTION_SYSTEM_PROMPT = (
    "Ты — помощник, который извлекает заголовки разделов из текста тендерного "
    "документа (документация о закупке по 44-ФЗ/223-ФЗ). "
    "Найди все заголовки разделов/пунктов в тексте ниже. "
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


def split_by_headers(text: str, headers: List[str], doc_name: str = "") -> List[Section]:
    """
    Режет текст документа на секции по дословным заголовкам.

    Первая тестовая версия — БЕЗ fuzzy-matching: если заголовок не найден
    точным поиском (str.find, начиная с позиции конца предыдущего заголовка),
    выбрасывается HeaderNotFoundError. Это осознанное решение для первого
    прогона — если заголовки часто не находятся, это сразу видно и можно
    решать, добавлять ли fuzzy-порог.

    Секция = "от найденного заголовка до начала следующего заголовка",
    последняя секция — до конца документа. Текст ДО первого найденного
    заголовка (титульный лист, шапка) в секции не попадает.
    """
    if not headers:
        raise ValueError("Пустой список заголовков — нечего резать.")

    positions = []
    cursor = 0
    for header in headers:
        idx = text.find(header, cursor)
        if idx == -1:
            raise HeaderNotFoundError(header, doc_name)
        positions.append((header, idx))
        cursor = idx + len(header)

    sections = []
    for i, (header, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        sections.append(Section(header=header, start=start, end=end, text=text[start:end]))

    return sections
