# ============================================================
#  storage/drive.py — загрузка документов закупок на Google Диск
#
#  Что получается на Диске:
#    <DRIVE_FOLDER_NAME>/<номер закупки>/<документы> + _done.flag
#
#  Доступ — OAuth с правами drive.file: скрипт видит только файлы
#  и папки, которые создал сам, остальной Диск ему недоступен.
#  Поэтому корневую папку скрипт создаёт сам при первом запуске.
#  Потом её можно переместить в любое место Диска — она найдётся
#  по имени (скрипт ищет только среди своих папок).
# ============================================================

import mimetypes
import shutil
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from loguru import logger

import config


SCOPES      = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
RETRIES     = 5                     # повторы запроса при 5xx / 429 / обрыве
RESUMABLE_FROM = 5 * 1024 * 1024    # файлы больше 5 МБ — загрузка частями


class DriveError(Exception):
    """Google Диск не настроен или недоступен."""


def _quote(value: str) -> str:
    """Экранирует строку для запроса files.list."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveUploader:
    def __init__(self):
        self._service = None
        self._root_id = None

    # ── Подключение ──────────────────────────────────────────────────────────

    def connect(self) -> str:
        """Входит в Google, находит или создаёт корневую папку. Возвращает ссылку на неё."""
        creds = self._credentials()
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._root_id = self._ensure_folder(config.DRIVE_FOLDER_NAME, parent_id=None)
        url = f"https://drive.google.com/drive/folders/{self._root_id}"
        logger.info(f"☁️  Google Диск: папка «{config.DRIVE_FOLDER_NAME}» → {url}")
        return url

    def _credentials(self) -> Credentials:
        token_file = config.DRIVE_TOKEN_FILE
        creds = None
        if token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
            except ValueError:
                logger.warning(f"{token_file.name} повреждён — нужно войти заново")

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                logger.warning("Вход в Google устарел или отозван — нужно войти заново")
                creds = None

        if not creds or not creds.valid:
            creds = self._login()

        token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _login(self) -> Credentials:
        secrets = config.DRIVE_CREDENTIALS_FILE
        if not secrets.exists():
            raise DriveError(
                f"не найден {secrets} — это OAuth-клиент из Google Cloud Console "
                f"(как получить — README.md, раздел «Google Диск»)"
            )
        logger.info("🔑 Открываю браузер для входа в Google (нужно один раз)...")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        return flow.run_local_server(
            port=0,
            authorization_prompt_message="Если браузер не открылся, перейди по ссылке:\n{url}",
            success_message="Готово, доступ к Google Диску получен. Окно можно закрыть.",
        )

    # ── Папки и файлы на Диске ───────────────────────────────────────────────

    def _find_folder(self, name: str, parent_id: str | None) -> str | None:
        q = f"name = '{_quote(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        res = self._service.files().list(
            q=q, spaces="drive", fields="files(id)",
            orderBy="createdTime", pageSize=1,
        ).execute(num_retries=RETRIES)
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def _ensure_folder(self, name: str, parent_id: str | None) -> str:
        folder_id = self._find_folder(name, parent_id)
        if folder_id:
            return folder_id
        body = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        folder = self._service.files().create(
            body=body, fields="id"
        ).execute(num_retries=RETRIES)
        return folder["id"]

    def _list_names(self, folder_id: str) -> set[str]:
        names, page_token = set(), None
        while True:
            res = self._service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                spaces="drive", fields="nextPageToken, files(name)",
                pageSize=1000, pageToken=page_token,
            ).execute(num_retries=RETRIES)
            names.update(f["name"] for f in res.get("files", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                return names

    def _upload_file(self, path: Path, folder_id: str) -> None:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(
            str(path), mimetype=mime,
            resumable=path.stat().st_size > RESUMABLE_FROM,
        )
        self._service.files().create(
            body={"name": path.name, "parents": [folder_id]},
            media_body=media, fields="id",
        ).execute(num_retries=RETRIES)

    # ── Загрузка папки закупки ───────────────────────────────────────────────

    def upload_tender(self, tender_dir: Path) -> bool:
        """
        Загружает docs/<номер>/ в <DRIVE_FOLDER_NAME>/<номер>/.
        Файлы, которые уже есть на Диске (по имени), не дублируются,
        поэтому повторный вызов безопасен. True — всё загружено.
        """
        marker = tender_dir / config.UPLOADED_FLAG_NAME
        marker.unlink(missing_ok=True)

        service_files = {config.DONE_FLAG_NAME, config.UPLOADED_FLAG_NAME}
        files = sorted(
            f for f in tender_dir.iterdir()
            if f.is_file()
            and f.name not in service_files
            and not f.name.endswith(".crdownload")
        )
        if not files:
            logger.info(f"  ☁️  {tender_dir.name}: документов нет — на Диск нечего загружать")
            self._finish(tender_dir, marker)
            return True

        uploaded = 0
        try:
            folder_id = self._ensure_folder(tender_dir.name, self._root_id)
            existing = self._list_names(folder_id)
            for f in files:
                if f.name in existing:
                    continue
                self._upload_file(f, folder_id)
                uploaded += 1
            # Флаг готовности — последним: если он есть на Диске,
            # значит все документы закупки уже там.
            done_flag = tender_dir / config.DONE_FLAG_NAME
            if done_flag.exists() and done_flag.name not in existing:
                self._upload_file(done_flag, folder_id)
        except Exception as e:
            logger.error(
                f"  ☁️  {tender_dir.name}: ошибка загрузки на Диск ({uploaded} из "
                f"{len(files)} загружено) — {e}. Догружу при следующем запуске"
            )
            return False

        skipped = len(files) - uploaded
        logger.info(
            f"  ☁️  {tender_dir.name}: на Диск загружено файлов: {uploaded}"
            + (f" (ещё {skipped} уже были там)" if skipped else "")
        )
        self._finish(tender_dir, marker)
        return True

    def _finish(self, tender_dir: Path, marker: Path) -> None:
        if config.DRIVE_KEEP_LOCAL:
            marker.write_text("", encoding="utf-8")
        else:
            shutil.rmtree(tender_dir, ignore_errors=True)
