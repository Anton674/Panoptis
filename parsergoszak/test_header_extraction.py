"""
Standalone-тест: прогоняет extract_headers + split_by_headers на папке
уже скачанных документов — без запуска основного агента (main.py/watcher.py).

Использование (PowerShell):
    python test_header_extraction.py --docs-dir "D:\\path\\to\\docs" --out-dir "D:\\path\\to\\out"

Требует:
    - запущенный llama.cpp сервер с Qwen на указанном --api-url, например:
        llama-server -m qwen2.5-7b-instruct-q4.gguf -c 32768 --port 8080
      (значение -c подбери под реальный размер документов; полные 120k не
      обязательны, если документы короче)
    - извлечение текста из файлов — см. TODO в load_text() ниже: пока
      обработан только .txt для быстрого теста; чтобы прогнать реальные
      .docx/.doc/.rtf/.odt/.xlsx из скачанных тендеров, подключи сюда
      существующий text_extractor.py из основного проекта.

Поведение при ошибках:
    Если для документа заголовок не находится точным поиском (без fuzzy),
    скрипт печатает ошибку по этому документу и идёт дальше — так по итогу
    прогона на всей папке сразу видно, на скольких документах точный матчинг
    не сработал. Если нужно наоборот — падать на первой же ошибке, поменяй
    поведение в блоке try/except в конце файла.
"""

import argparse
import json
import sys
from pathlib import Path

# Консоль Windows по умолчанию открывает stdout в кодировке cp1251/cp866,
# которая не может закодировать часть символов, встречающихся в реальных
# документах закупок (например, юникодный минус "−" U+2212 в диапазонах
# вида "≥15 и ≤35"). Без reconfigure любой print() с таким символом валит
# UnicodeEncodeError и обрывает весь прогон по папке на середине.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from header_splitter import HeaderNotFoundError, extract_headers, split_by_headers

sys.path.insert(0, str(Path(__file__).parent / "orchestrator"))
from text_extractor import extract_docx_text, extract_doc_text  # noqa: E402

# Извлекаем текст напрямую через функции text_extractor.py, а НЕ через
# text_extractor.extract_text() — та обрезает текст до config.MAX_DOC_CHARS
# (12000 символов), откалиброванного под контекст Aura (-c 8192). Qwen
# используется здесь именно из-за большого контекста, обрезка по чужому
# лимиту скрывала бы от неё заголовки в хвосте документа.
_LOADERS = {
    ".docx": extract_docx_text,
    ".doc": extract_doc_text,
}


def load_text(path: Path) -> str:
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        raise NotImplementedError(
            f"Расширение {path.suffix} не поддержано в тестовом скрипте "
            f"(поддержаны: {', '.join(_LOADERS)})."
        )
    return loader(path)


def process_document(path: Path, api_url: str, out_dir: Path) -> None:
    print(f"\n=== {path.name} ===")
    text = load_text(path)
    print(f"  Текст извлечён: {len(text)} символов")

    headers = extract_headers(text, api_url=api_url)
    print(f"  Qwen нашёл заголовков: {len(headers)}")
    for h in headers:
        print(f"    - {h}")

    sections = split_by_headers(text, headers, doc_name=path.name)
    print(f"  Разрезано на {len(sections)} секций")

    result = {
        "doc": path.name,
        "headers": headers,
        "sections": [
            {
                "header": s.header,
                "start": s.start,
                "end": s.end,
                "length": len(s.text),
                "text": s.text,
            }
            for s in sections
        ],
    }
    out_path = out_dir / f"{path.parent.name}__{path.stem}.sections.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Сохранено: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Тест извлечения заголовков разделов через Qwen")
    parser.add_argument("--docs-dir", required=True, help="Папка со скачанными документами")
    parser.add_argument("--out-dir", default="./header_test_output", help="Куда сохранять результаты")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8080/v1/chat/completions",
        help="Адрес llama.cpp сервера с Qwen (OpenAI-совместимый endpoint)",
    )
    parser.add_argument(
        "--ext",
        default=None,
        help=f"Расширение файлов для обработки (по умолчанию все поддержанные: {', '.join(_LOADERS)})",
    )
    args = parser.parse_args()

    docs_dir = Path(args.docs_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exts = [args.ext] if args.ext else list(_LOADERS)
    # rglob — документы лежат в подпапках docs/<reg_number>/..., не плоско
    files = sorted(
        p for ext in exts for p in docs_dir.rglob(f"*{ext}")
    )
    if not files:
        print(f"Файлов с расширениями {exts} в {docs_dir} (включая подпапки) не найдено.")
        sys.exit(1)

    ok, failed = 0, 0
    for path in files:
        try:
            process_document(path, args.api_url, out_dir)
            ok += 1
        except HeaderNotFoundError as e:
            print(f"  ОШИБКА (заголовок не найден): {e}")
            failed += 1
        except Exception as e:
            print(f"  ОШИБКА: {type(e).__name__}: {e}")
            failed += 1

    print(f"\nИтого: {ok} успешно, {failed} с ошибкой из {len(files)} файлов")


if __name__ == "__main__":
    main()
