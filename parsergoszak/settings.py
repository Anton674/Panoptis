# ============================================================
#  settings.py — настройки из settings.toml и параметров запуска
#
#  Приоритет: параметры запуска > settings.toml > config.py
#  Значения записываются прямо в модуль config, поэтому остальной
#  код по-прежнему читает config.SEARCH_KEYWORDS, config.LAWS и т.д.
# ============================================================

import re
import tomllib
from pathlib import Path

from loguru import logger

import config


class SettingsError(Exception):
    """Ошибка в settings.toml или в параметрах запуска."""


# ── Проверка и приведение значений ───────────────────────────────────────────

def _to_str(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("нужна непустая строка в кавычках")
    return value.strip()


def _to_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("нужно целое число")
    if value < 1:
        raise ValueError("нужно число больше нуля")
    return value


def _to_bool(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("нужно true или false")
    return value


def _to_path(value) -> Path:
    path = Path(value if isinstance(value, Path) else _to_str(value)).expanduser()
    # относительные пути — от папки парсера, а не от того, откуда запущен скрипт
    return path if path.is_absolute() else config.BASE_DIR / path


def _to_keywords(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(w, str) for w in value):
        raise ValueError('нужен список строк, например ["бумага а4", "картридж"]')
    words = [w.strip() for w in value if w.strip()]
    if not words:
        raise ValueError("список ключевых слов пуст")
    return words


def _to_laws(value) -> list[str]:
    # Принимает 44, "44", "44-ФЗ", "fz44", "44,223" — и в списке, и одиночным значением
    items = value if isinstance(value, list) else [value]
    laws = []
    for item in items:
        numbers = re.findall(r"\d+", str(item))
        if not numbers:
            raise ValueError(f"не понял закон {item!r} — пиши 44 или 223")
        for num in numbers:
            code = f"fz{num}"
            if code not in config.LAW_LABELS:
                raise ValueError(f"неизвестный закон {num} — поддерживаются 44 и 223")
            if code not in laws:
                laws.append(code)
    return laws


def _choice(*allowed: str, upper: bool = False):
    def convert(value) -> str:
        v = _to_str(value)
        v = v.upper() if upper else v.lower()
        if v not in allowed:
            raise ValueError("допустимо: " + ", ".join(allowed))
        return v
    return convert


def _to_records_per_page(value) -> int:
    v = _to_int(value)
    if v not in (10, 20, 50):
        raise ValueError("допустимо 10, 20 или 50")
    return v


# имя в settings.toml → (атрибут в config.py, проверка)
SETTINGS = {
    "target":           ("TARGET",                 _choice("drive", "local")),
    "keywords":         ("SEARCH_KEYWORDS",        _to_keywords),
    "laws":             ("LAWS",                   _to_laws),
    "max_pages":        ("MAX_PAGES",              _to_int),
    "records_per_page": ("RECORDS_PER_PAGE",       _to_records_per_page),
    "sort_by":          ("SORT_BY",                _choice("PUBLISH_DATE", "UPDATE_DATE",
                                                           "PRICE", upper=True)),
    "download_limit":   ("DOWNLOAD_LIMIT",         _to_int),
    "docs_dir":         ("DOCS_DIR",               _to_path),
    "headless":         ("HEADLESS",               _to_bool),
    "drive_folder":     ("DRIVE_FOLDER_NAME",      _to_str),
    "keep_local_copy":  ("DRIVE_KEEP_LOCAL",       _to_bool),
    "credentials_file": ("DRIVE_CREDENTIALS_FILE", _to_path),
    "token_file":       ("DRIVE_TOKEN_FILE",       _to_path),
}


# ── Загрузка ─────────────────────────────────────────────────────────────────

def _read_file(path: Path) -> dict:
    try:
        # utf-8-sig — на случай, если Блокнот сохранил файл с BOM
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as e:
        raise SettingsError(f"{path.name}: сохрани файл в кодировке UTF-8") from e
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path.name}: ошибка в записи — {e}") from e

    for key in sorted(set(data) - set(SETTINGS)):
        logger.warning(f"{path.name}: неизвестный параметр «{key}» — пропускаю")
    return {k: v for k, v in data.items() if k in SETTINGS}


def _apply(values: dict, source: str) -> None:
    for key, value in values.items():
        attr, convert = SETTINGS[key]
        try:
            setattr(config, attr, convert(value))
        except ValueError as e:
            raise SettingsError(f"{source}: {key} = {value!r} — {e}") from e


def load(path: Path | None, overrides: dict) -> None:
    """
    path      — файл настроек из --config (None → settings.toml рядом с main.py)
    overrides — значения из параметров запуска; None означает «не указано»
    """
    settings_file = path or config.SETTINGS_FILE
    if settings_file.exists():
        _apply(_read_file(settings_file), settings_file.name)
        logger.info(f"Настройки: {settings_file}")
    elif path:
        raise SettingsError(f"файл настроек не найден: {settings_file}")
    else:
        logger.warning(f"{settings_file.name} не найден — беру значения из config.py")

    _apply({k: v for k, v in overrides.items() if v is not None}, "параметры запуска")
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
