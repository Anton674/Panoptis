"""Распознавание текста на фотографиях.

Скрипт следит за папкой «Фото» и для каждой новой картинки создаёт
текстовый файл с тем же именем в папке «Текст».

Запуск:
    python ocr.py                  — обработать всё и дальше следить за папкой
    python ocr.py --once           — обработать то, что есть, и выйти
    python ocr.py --download-models — только скачать модели (используется установщиком)
"""

import argparse
import glob
import logging
import sys
import time
import warnings
from pathlib import Path

# Технические предупреждения PyTorch только пугают пользователя.
warnings.filterwarnings("ignore", category=UserWarning)

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "Фото"
OUTPUT_DIR = BASE_DIR / "Текст"
MODELS_DIR = BASE_DIR / "models"
LOG_FILE = BASE_DIR / "ocr.log"

LANGUAGES = ["ru", "en"]
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif",
}
POLL_SECONDS = 3

log = logging.getLogger("ocr")


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%d.%m.%Y %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file.setFormatter(fmt)
    log.addHandler(console)
    log.addHandler(file)
    log.setLevel(logging.INFO)


def create_reader():
    import easyocr

    MODELS_DIR.mkdir(exist_ok=True)
    return easyocr.Reader(
        LANGUAGES,
        gpu=False,
        model_storage_directory=str(MODELS_DIR),
        user_network_directory=str(MODELS_DIR),
        verbose=False,
    )


def load_image(path: Path):
    """Открывает картинку через Pillow: так работают русские пути на Windows,
    HEIC с iPhone и поворот по EXIF (фото с телефона часто «лежат на боку»)."""
    import numpy as np
    from PIL import Image, ImageOps

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass

    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return np.array(img.convert("RGB"))


def recognize(reader, path: Path) -> str:
    paragraphs = reader.readtext(load_image(path), detail=0, paragraph=True)
    return "\n\n".join(p.strip() for p in paragraphs if p.strip())


def output_path_for(image: Path) -> Path:
    """IMG_1.jpg → IMG_1.txt. Если рядом есть IMG_1.png, имена расходятся:
    IMG_1 (jpg).txt и IMG_1 (png).txt — иначе одно фото затёрло бы другое."""
    twins = [
        p for p in INPUT_DIR.glob(glob.escape(image.stem) + ".*")
        if p != image and p.stem == image.stem and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    name = f"{image.stem} ({image.suffix.lstrip('.').lower()})" if twins else image.stem
    return OUTPUT_DIR / (name + ".txt")


def is_pending(image: Path) -> bool:
    """Нужно ли (пере)распознать картинку: текста ещё нет или фото новее текста."""
    txt = output_path_for(image)
    return not txt.exists() or txt.stat().st_mtime < image.stat().st_mtime


def is_fully_copied(image: Path) -> bool:
    """Ждём, пока файл докопируется: размер не меняется за секунду."""
    size = image.stat().st_size
    time.sleep(1)
    return size > 0 and image.stat().st_size == size


def find_images() -> list[Path]:
    return sorted(
        p for p in INPUT_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def process_pending(reader, failed: dict[Path, float]) -> int:
    done = 0
    for image in find_images():
        mtime = None
        try:
            if not is_pending(image):
                continue
            mtime = image.stat().st_mtime
            # Не долбимся в один и тот же битый файл: повторяем, только если его заменили.
            if failed.get(image) == mtime:
                continue
            if not is_fully_copied(image):
                continue

            log.info("Распознаю: %s", image.name)
            started = time.time()
            text = recognize(reader, image)
            output_path_for(image).write_text(text + "\n", encoding="utf-8")
            failed.pop(image, None)
            done += 1
            if text:
                log.info("Готово за %.0f с → %s", time.time() - started, output_path_for(image).name)
            else:
                log.info("Текст не найден, создан пустой файл %s", output_path_for(image).name)
        except FileNotFoundError:
            # Файл удалили или переименовали, пока мы его обрабатывали.
            continue
        except Exception as exc:  # одна плохая картинка не должна останавливать остальные
            if mtime is not None:
                failed[image] = mtime
            log.error("Не удалось обработать %s: %s", image.name, exc)
    return done


def make_shortcuts() -> None:
    """Ярлыки на рабочем столе (только Windows). PowerShell получает команду
    в -EncodedCommand, чтобы русские имена не ломались из-за кодировки консоли."""
    if sys.platform != "win32":
        return
    import base64
    import subprocess

    def quote(value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$run = $shell.CreateShortcut((Join-Path $desktop 'Распознать текст с фото.lnk'))
$run.TargetPath = {quote(BASE_DIR / "run.bat")}
$run.WorkingDirectory = {quote(BASE_DIR)}
$run.IconLocation = 'shell32.dll,22'
$run.Save()
$photos = $shell.CreateShortcut((Join-Path $desktop 'Фото для распознавания.lnk'))
$photos.TargetPath = {quote(INPUT_DIR)}
$photos.Save()
$texts = $shell.CreateShortcut((Join-Path $desktop 'Распознанный текст.lnk'))
$texts.TargetPath = {quote(OUTPUT_DIR)}
$texts.Save()
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
    )
    if result.returncode == 0:
        log.info("Ярлыки созданы на рабочем столе.")
    else:
        log.error("Не удалось создать ярлыки. Программу можно запускать файлом run.bat в папке %s", BASE_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Распознавание текста на фото")
    parser.add_argument("--once", action="store_true", help="обработать текущие файлы и выйти")
    parser.add_argument("--download-models", action="store_true", help="только скачать модели")
    parser.add_argument("--make-shortcuts", action="store_true", help="создать ярлыки на рабочем столе")
    parser.add_argument("--open-folder", action="store_true", help="открыть папку с фото при запуске")
    args = parser.parse_args()

    setup_logging()
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.make_shortcuts:
        make_shortcuts()
        return
    if args.open_folder and sys.platform == "win32":
        import os

        os.startfile(INPUT_DIR)

    log.info("Загружаю модели распознавания (в первый раз они скачиваются, это несколько минут)...")
    reader = create_reader()
    if args.download_models:
        log.info("Модели готовы.")
        return

    failed: dict[Path, float] = {}
    process_pending(reader, failed)
    if args.once:
        return

    log.info("Жду новые фото в папке: %s", INPUT_DIR)
    log.info("Чтобы остановить, закройте это окно.")
    while True:
        time.sleep(POLL_SECONDS)
        try:
            process_pending(reader, failed)
        except OSError as exc:
            log.error("Ошибка доступа к папке: %s", exc)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
