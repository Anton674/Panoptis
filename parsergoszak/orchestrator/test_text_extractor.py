"""
test_text_extractor.py — прогоняет extract_text() по реальным файлам
в DOCS_DIR (или по пути, переданному аргументом) и печатает отчёт:
какие файлы распознались, сколько символов текста вытащено, где
случилась ошибка/пропуск.

Ничего не пишет в БД и не трогает LLM — только текстовый слой,
это быстрая офлайн-проверка перед полным запуском watcher.py + main.py.

Запуск (из папки orchestrator, при активированном venv):

    # проверить все закупки в config.DOCS_DIR
    python test_text_extractor.py

    # проверить только один файл
    python test_text_extractor.py "C:\\Panoptis\\parsergoszak\\docs\\0166100000726000015\\Проект контракта.doc"

    # проверить одну папку закупки
    python test_text_extractor.py "C:\\Panoptis\\parsergoszak\\docs\\0166100000726000015"

Перед первым запуском убедись, что:
  1. Установлены зависимости: pip install -r requirements.txt
  2. Установлен LibreOffice (нужен для .doc/.xls) и soffice виден
     в PATH — проверить в PowerShell: soffice --version
     Если не виден — пропиши полный путь в config.LIBREOFFICE_PATH.
"""

import sys
from pathlib import Path

import config
import text_extractor


def _iter_target_files(target: Path):
    """Возвращает список файлов для проверки: сам файл, либо все файлы
    внутри папки (рекурсивно), кроме _done.flag."""
    if target.is_file():
        return [target]

    if target.is_dir():
        return sorted(
            f for f in target.rglob("*")
            if f.is_file() and f.name != config.DONE_FLAG_NAME
        )

    return []


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else config.DOCS_DIR

    if not target.exists():
        print(f"❌ Путь не существует: {target}")
        sys.exit(1)

    files = _iter_target_files(target)
    if not files:
        print(f"Файлы не найдены в {target}")
        return

    print(f"Проверяю {len(files)} файл(ов) в {target}\n")

    ok, empty, unsupported, failed = 0, 0, 0, 0

    for f in files:
        ext = f.suffix.lower()
        marker_supported = ext in config.SUPPORTED_EXTENSIONS

        if not marker_supported:
            print(f"⏭️  [{ext or 'без расширения'}] {f.name} — не поддерживается, пропуск")
            unsupported += 1
            continue

        try:
            text = text_extractor.extract_text(f)
        except Exception as e:
            print(f"❌ [{ext}] {f.name} — необработанное исключение: {e}")
            failed += 1
            continue

        if text is None:
            print(f"❌ [{ext}] {f.name} — extract_text вернул None (см. лог выше)")
            failed += 1
        elif not text.strip():
            print(f"◻️  [{ext}] {f.name} — извлечён пустой текст")
            empty += 1
        else:
            preview = text[:80].replace("\n", " ")
            print(f"✅ [{ext}] {f.name} — {len(text)} симв. | «{preview}...»")
            ok += 1

    print(
        f"\nИтого: ✅ {ok}  ◻️ пусто {empty}  "
        f"⏭️ не поддержано {unsupported}  ❌ ошибка {failed}  "
        f"(всего {len(files)})"
    )


if __name__ == "__main__":
    main()
