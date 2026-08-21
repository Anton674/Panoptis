"""
Юнит-тесты для чистой логики header_splitter.split_by_headers() —
без LLM и без сети (в отличие от test_header_extraction.py, который
гоняет extract_headers() через реальный llama.cpp сервер).

Запуск:
    python test_header_splitter.py
"""

import sys

# См. тот же workaround в test_header_extraction.py — Windows-консоль по
# умолчанию открывает stdout в cp1251, которая не кодирует эмодзи-маркеры
# результатов теста (✅/❌).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from header_splitter import HeaderNotFoundError, split_by_headers


def test_exact_match_basic_split():
    text = "Шапка\n\nРаздел 1\nтекст первого раздела\n\nРаздел 2\nтекст второго раздела"
    sections = split_by_headers(text, ["Раздел 1", "Раздел 2"])

    assert len(sections) == 2
    assert sections[0].header == "Раздел 1"
    assert sections[0].text.startswith("Раздел 1\nтекст первого раздела")
    assert sections[1].header == "Раздел 2"
    assert sections[1].text.startswith("Раздел 2\nтекст второго раздела")
    # текст до первого заголовка (шапка) не попадает ни в одну секцию
    assert "Шапка" not in sections[0].text


def test_whitespace_flexible_match():
    # в тексте перевод строки внутри заголовка там, где Qwen вернул пробел
    text = "Общие\nположения\nдоговора\n\nтекст раздела"
    sections = split_by_headers(text, ["Общие положения договора"])

    assert len(sections) == 1
    assert sections[0].header == "Общие положения договора"
    assert sections[0].start == 0


def test_case_insensitive_match():
    text = "ТЕХНИЧЕСКОЕ ЗАДАНИЕ\nсодержание задания"
    sections = split_by_headers(text, ["Техническое задание"])

    assert len(sections) == 1
    assert sections[0].start == 0


def test_fuzzy_match_for_minor_rephrasing():
    text = "Ответственность сторон.\nтекст про ответственность"
    # без точки на конце — точный и гибкий-по-пробелам поиск не найдут,
    # должен сработать rapidfuzz
    sections = split_by_headers(text, ["Ответственность сторон"])

    assert len(sections) == 1


def test_header_not_found_raises():
    text = "Раздел A\nтекст"
    try:
        split_by_headers(text, ["Раздел, которого нет в документе"])
    except HeaderNotFoundError:
        pass
    else:
        raise AssertionError("Ожидался HeaderNotFoundError")


def test_out_of_order_header_is_sorted_by_position():
    # Qwen вернул заголовки не в порядке появления в тексте — на выходе
    # секции должны быть отсортированы по фактической позиции в документе.
    text = "Приложение № 1\nтекст приложения\n\nАдреса Сторон\nтекст адресов"
    sections = split_by_headers(text, ["Адреса Сторон", "Приложение № 1"])

    assert [s.header for s in sections] == ["Приложение № 1", "Адреса Сторон"]


def test_empty_headers_list_raises():
    try:
        split_by_headers("любой текст", [])
    except ValueError:
        pass
    else:
        raise AssertionError("Ожидался ValueError для пустого списка заголовков")


def main() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_") and callable(obj)]
    ok, failed = 0, 0
    for test in tests:
        try:
            test()
            print(f"✅ {test.__name__}")
            ok += 1
        except Exception as e:
            print(f"❌ {test.__name__} — {type(e).__name__}: {e}")
            failed += 1

    print(f"\nИтого: {ok} прошло, {failed} упало из {len(tests)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
