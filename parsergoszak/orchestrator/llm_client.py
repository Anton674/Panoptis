"""
llm_client.py — клиент для локального llama.cpp сервера (Aura 7B).

Каждый вызов analyze_document() — полностью самостоятельный запрос
к /completion, без истории сообщений. Это не только логично для
последовательной обработки разных файлов, но и обходит стороной
замеченный баг со сбоями модели на втором сообщении в диалоге:
раз диалога как такового нет, нечему сбоить.
"""

import json
from pathlib import Path

import requests
from loguru import logger

import config


PROMPT_TEMPLATE = """Ты — помощник по анализу документов государственных закупок (44-ФЗ/223-ФЗ).

Ниже приведён текст документа закупки. Найди в нём информацию об объекте закупки — товарах, их количестве и единицах измерения.

Верни ответ строго в формате JSON, без пояснений и текста до/после, по схеме:

{{
  "found": true или false,
  "items": [
    {{"name": "название товара", "quantity": число или null, "unit": "единица измерения"}}
  ]
}}

Если в документе нет информации об объекте закупки (товарах/количестве) — верни {{"found": false, "items": []}}.
Не придумывай данные, которых нет в документе.

Текст документа:
\"\"\"
{document_text}
\"\"\"
"""

_GRAMMAR_TEXT = (Path(__file__).parent / "grammar.gbnf").read_text(encoding="utf-8")


class LLMError(Exception):
    pass


def _call_llm(prompt: str) -> str:
    """Один запрос к /completion. Возвращает сырую строку ответа модели."""
    payload = {
        "prompt": prompt,
        "grammar": _GRAMMAR_TEXT,
        "n_predict": config.LLM_N_PREDICT,
        "temperature": config.LLM_TEMPERATURE,
        "top_p": config.LLM_TOP_P,
        "repeat_penalty": config.LLM_REPEAT_PENALTY,
        # Каждый запрос независим — не хотим, чтобы сервер переиспользовал
        # kv-cache предыдущего запроса как "контекст диалога".
        "cache_prompt": False,
    }

    try:
        resp = requests.post(config.LLM_URL, json=payload, timeout=config.LLM_TIMEOUT_SEC)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"Ошибка обращения к LLM-серверу: {e}") from e

    data = resp.json()
    content = data.get("content", "")
    if not content:
        raise LLMError(f"Пустой ответ от LLM-сервера: {data}")
    return content


def analyze_document(document_text: str, source_file: str) -> dict:
    """
    Отправляет текст документа на анализ и возвращает распарсенный
    словарь вида {"found": bool, "items": [...]}, а также сырой ответ
    модели под ключом "_raw" (для записи в raw_llm_response).

    Делает до config.MAX_JSON_RETRIES дополнительных попыток при
    невалидном JSON в ответе (сама грамматика GBNF должна гарантировать
    валидность синтаксиса, но не гарантирует смысловую адекватность —
    ретрай защищает от редких сбоев генерации).
    """
    prompt = PROMPT_TEMPLATE.format(document_text=document_text)

    last_error = None
    for attempt in range(1 + config.MAX_JSON_RETRIES):
        try:
            raw = _call_llm(prompt)
        except LLMError as e:
            last_error = str(e)
            logger.error(f"  [{source_file}] попытка {attempt + 1}: {e}")
            continue

        try:
            parsed = json.loads(raw)
            if "found" not in parsed or "items" not in parsed:
                raise ValueError("В ответе отсутствуют обязательные поля found/items")
            parsed["_raw"] = raw
            return parsed
        except (json.JSONDecodeError, ValueError) as e:
            last_error = f"Невалидный JSON от модели: {e}. Сырой ответ: {raw[:500]}"
            logger.warning(f"  [{source_file}] попытка {attempt + 1}: {last_error}")
            continue

    raise LLMError(last_error or "Не удалось получить валидный ответ от LLM")
