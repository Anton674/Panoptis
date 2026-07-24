"""
test_grammar.py — разовая проверка grammar.gbnf на локальном llama.cpp сервере.

Запуск (из папки orchestrator, при активированном venv):
    python test_grammar.py
"""

import json
import requests

GRAMMAR_PATH = "grammar.gbnf"
LLM_URL = "http://127.0.0.1:8080/completion"

with open(GRAMMAR_PATH, "r", encoding="utf-8") as f:
    grammar = f.read()

prompt = (
    "Верни JSON с found=true и одним товаром: "
    "название 'Бумага А4', количество 10, единица измерения 'шт'."
)

payload = {
    "prompt": prompt,
    "grammar": grammar,
    "n_predict": 200,
    "temperature": 0.1,
}

resp = requests.post(LLM_URL, json=payload, timeout=60)

print("HTTP статус:", resp.status_code)
print("Сырой ответ сервера:")
print(resp.text)

if resp.ok:
    data = resp.json()
    content = data.get("content", "")
    print("\n--- content от модели ---")
    print(content)
    try:
        parsed = json.loads(content)
        print("\n✅ JSON валиден:", parsed)
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON невалиден: {e}")
