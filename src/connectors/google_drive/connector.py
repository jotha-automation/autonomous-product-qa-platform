"""Google Drive connector — mock fallback + real Drive API v3."""
import asyncio
import io
import logging
import os
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from src.connectors.google_drive.credentials import (
    drive_http_timeout_seconds,
    get_service_account_credentials,
    load_credentials_json,
    warmup_service_account_credentials,
)

logger = logging.getLogger("google_drive")

from src.intake.file_types import (
    GOOGLE_EXPORT_MIME,
    SUPPORTED_BINARY_MIMES,
    guess_mime_from_filename,
    is_intake_file,
)
from src.runtime_policy import allow_mocks, drive_enabled, ensure_drive_configured

# Back-compat alias for modules that import EXPORT_MIME from the connector
EXPORT_MIME = GOOGLE_EXPORT_MIME

_DEFAULT_MOCK_SYSTEM_ROOT = Path("tests/fixtures/mock-drive/APTP")
_DEFAULT_MOCK_FIXTURES = Path("tests/fixtures/documents")


def resolve_mock_system_root() -> Path:
    """Local path for the APTP system root folder (mock Drive only)."""
    explicit = (os.getenv("APTP_MOCK_SYSTEM_ROOT") or "").strip()
    if explicit:
        return Path(explicit)
    return _DEFAULT_MOCK_SYSTEM_ROOT


def _find_project_folder_under_system(system_root: Path, folder_id: str) -> Optional[Path]:
    fid = folder_id.lower()
    child = system_root / folder_id.replace("mock-", "")
    if child.is_dir():
        return child
    for sub in system_root.iterdir() if system_root.is_dir() else []:
        if sub.is_dir() and (sub.name in folder_id or fid in sub.name.lower()):
            return sub
    if "portfolio" in fid or "jsh" in fid:
        for sub in system_root.iterdir() if system_root.is_dir() else []:
            if sub.is_dir() and ("portfolio" in sub.name.lower() or "jsh" in sub.name.lower()):
                return sub
    return None


def resolve_mock_drive_root(folder_id: str) -> Path:
    """Map mock Drive folder IDs to local fixture trees."""
    fid = folder_id.lower()

    if "system" in fid or fid in {"mock-aptp", "aptp"}:
        return resolve_mock_system_root()

    system_root = resolve_mock_system_root()
    match = _find_project_folder_under_system(system_root, folder_id)
    if match is not None:
        return match

    env_root = (os.getenv("APTP_MOCK_DRIVE_ROOT") or "").strip()
    if env_root:
        return Path(env_root)

    return _DEFAULT_MOCK_FIXTURES


class MockGoogleDriveConnector:
    """Simulates Drive folder sync for local dev when credentials are absent."""

    def __init__(self, fixtures_dir: Optional[Path] = None):
        self.fixtures_dir = fixtures_dir or _DEFAULT_MOCK_FIXTURES

    def _root_for(self, folder_id: str) -> Path:
        candidate = resolve_mock_drive_root(folder_id)
        return candidate if candidate.exists() else self.fixtures_dir

    async def list_files(self, folder_id: str) -> List[Dict[str, Any]]:
        root = self._root_for(folder_id)
        if not root.exists():
            return []
        return [
            {
                "id": f"mock-{entry['relative_path']}",
                "name": entry["name"],
                "mimeType": entry.get("mimeType", "application/octet-stream"),
                "relative_path": entry["relative_path"],
                "path": entry.get("path"),
                "modifiedTime": entry.get("modifiedTime"),
                "size": entry.get("size"),
            }
            for entry in self._walk_local(root)
        ]

    def _walk_local(self, root: Path) -> List[Dict[str, Any]]:
        from src.intake.discovery import _SKIP_DIR_NAMES

        entries: List[Dict[str, Any]] = []
        import os

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
            for fname in filenames:
                path = Path(dirpath) / fname
                if not is_intake_file(fname):
                    continue
                rel = path.relative_to(root).as_posix()
                stat = path.stat()
                entries.append(
                    {
                        "name": fname,
                        "relative_path": rel,
                        "mimeType": guess_mime_from_filename(fname),
                        "path": str(path),
                        "modifiedTime": str(int(stat.st_mtime)),
                        "size": stat.st_size,
                    }
                )
        return entries

    async def list_files_recursive(self, folder_id: str) -> List[Dict[str, Any]]:
        return await self.list_files(folder_id)

    async def list_child_folders(self, folder_id: str) -> List[Dict[str, Any]]:
        root = self._root_for(folder_id)
        if not root.exists():
            return []
        import os

        folders = []
        for name in sorted(os.listdir(root)):
            path = root / name
            if path.is_dir() and not name.startswith("."):
                stat = path.stat()
                folders.append(
                    {
                        "id": f"mock-{name}",
                        "name": name,
                        "mimeType": "application/vnd.google-apps.folder",
                        "modifiedTime": str(int(stat.st_mtime)),
                    }
                )
        return folders

    async def list_folder_children(self, folder_id: str) -> List[Dict[str, Any]]:
        root = self._root_for(folder_id)
        if not root.exists():
            return []
        import os

        items = []
        for name in sorted(os.listdir(root)):
            path = root / name
            if name.startswith("."):
                continue
            stat = path.stat()
            if path.is_dir():
                items.append(
                    {
                        "id": f"mock-{path.relative_to(resolve_mock_system_root()).as_posix()}",
                        "name": name,
                        "mimeType": "application/vnd.google-apps.folder",
                        "modifiedTime": str(int(stat.st_mtime)),
                    }
                )
            else:
                rel = path.relative_to(resolve_mock_system_root()).as_posix()
                items.append(
                    {
                        "id": f"mock-{rel}",
                        "name": name,
                        "mimeType": "application/octet-stream",
                        "modifiedTime": str(int(stat.st_mtime)),
                        "size": stat.st_size,
                    }
                )
        return items

    def _mock_search_roots(self) -> List[Path]:
        system_root = resolve_mock_system_root()
        roots: List[Path] = [system_root, self.fixtures_dir]
        if system_root.is_dir():
            for child in system_root.iterdir():
                if child.is_dir():
                    roots.insert(0, child)
        return roots

    async def download_file(self, file_id: str) -> bytes:
        rel = file_id.replace("mock-", "", 1)
        search_roots = self._mock_search_roots()
        for root in search_roots:
            path = root / rel
            if path.is_file():
                return path.read_bytes()
        return b""

    async def get_file_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        rel = file_id.replace("mock-", "", 1)
        for root in self._mock_search_roots():
            if not root.exists():
                continue
            if root.is_dir():
                for child in [root, *root.iterdir()]:
                    if child.is_dir():
                        path = child / rel if "/" in rel else child / rel
                        if path.is_file():
                            return {
                                "id": file_id,
                                "name": path.name,
                                "mimeType": "application/octet-stream",
                                "relative_path": rel,
                            }
        return None

    def _path_for_id(self, folder_id: str) -> Path:
        return self._root_for(folder_id)

    def _id_for_path(self, path: Path) -> str:
        system = resolve_mock_system_root()
        try:
            rel = path.relative_to(system).as_posix()
            return f"mock-{rel}"
        except ValueError:
            return f"mock-{path.name}"

    async def create_folder(self, parent_id: str, name: str) -> str:
        parent = self._path_for_id(parent_id)
        target = parent / name
        target.mkdir(parents=True, exist_ok=True)
        return self._id_for_path(target)

    async def upload_file(
        self, parent_id: str, filename: str, content: bytes, mime_type: str = "application/octet-stream"
    ) -> str:
        parent = self._path_for_id(parent_id)
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / filename
        path.write_bytes(content)
        return self._id_for_path(path)

    async def update_file(self, file_id: str, content: bytes, mime_type: str = "application/octet-stream") -> str:
        rel = file_id.replace("mock-", "", 1)
        path = resolve_mock_system_root() / rel
        if not path.is_file():
            for child in resolve_mock_system_root().iterdir():
                candidate = child / rel
                if candidate.is_file():
                    path = candidate
                    break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return file_id

    async def delete_file(self, file_id: str) -> None:
        import shutil

        rel = file_id.replace("mock-", "", 1)
        for base in [resolve_mock_system_root()]:
            path = base / rel
            if path.is_file():
                path.unlink()
                return
            if path.is_dir():
                shutil.rmtree(path)
                return
            for child in base.iterdir():
                if child.is_dir():
                    candidate = child / rel
                    if candidate.is_file():
                        candidate.unlink()
                        return
                    if candidate.is_dir():
                        shutil.rmtree(candidate)
                        return


def _build_drive_service(creds):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    timeout = drive_http_timeout_seconds()
    http = httplib2.Http(timeout=timeout)
    authorized = AuthorizedHttp(creds, http=http)
    return build("drive", "v3", http=authorized, cache_discovery=False)


def drive_operation_timeout_seconds() -> float:
    return float(max(10, int(os.getenv("GOOGLE_DRIVE_OPERATION_TIMEOUT", "90"))))


def drive_retry_attempts() -> int:
    return max(1, int(os.getenv("GOOGLE_DRIVE_RETRY_ATTEMPTS", "4")))


def drive_max_concurrency() -> int:
    return max(1, int(os.getenv("GOOGLE_DRIVE_MAX_CONCURRENCY", "2")))


# httplib2 connections are not thread-safe; serialize + cap concurrent executor jobs.
_DRIVE_API_LOCK = threading.Lock()
_DRIVE_CONCURRENCY_SEM: Optional[asyncio.Semaphore] = None

T = TypeVar("T")


def _get_drive_concurrency_sem() -> asyncio.Semaphore:
    global _DRIVE_CONCURRENCY_SEM
    if _DRIVE_CONCURRENCY_SEM is None:
        _DRIVE_CONCURRENCY_SEM = asyncio.Semaphore(drive_max_concurrency())
    return _DRIVE_CONCURRENCY_SEM


def _is_transient_ssl_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    message = str(exc).upper()
    return "SSL" in message and any(
        token in message
        for token in (
            "DECRYPTION_FAILED",
            "BAD_RECORD",
            "WRONG_VERSION",
            "UNEXPECTED_RECORD",
            "MIXED_HANDSHAKE",
            "ENCRYPTED_LENGTH",
        )
    )


def _is_transient_drive_error(exc: BaseException) -> bool:
    if _is_transient_ssl_error(exc):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "rate limit",
            "rateLimitExceeded",
            "user rate limit",
            "quota exceeded",
            "timeout",
            "timed out",
            "503",
            "500",
            "502",
            "504",
            "internal error",
            "backend error",
            "connection",
        )
    )


class GoogleDriveConnector:
    """Google Drive API v3 connector using a service account."""

    def __init__(self, credentials_json: Optional[str] = None):
        self.credentials_json = credentials_json or load_credentials_json()
        self._mock = MockGoogleDriveConnector()
        self._service = None
        self._service_lock = asyncio.Lock()

    def _ensure_service_unlocked(self) -> None:
        """Build Drive client; caller must hold _DRIVE_API_LOCK."""
        if self._service is not None or not self.credentials_json:
            return
        creds = get_service_account_credentials()
        if not creds:
            return
        warmup_service_account_credentials(creds)
        self._service = _build_drive_service(creds)
        logger.info(
            "Google Drive connector ready (service account, http_timeout=%ss)",
            drive_http_timeout_seconds(),
        )

    def _invalidate_service(self) -> None:
        with _DRIVE_API_LOCK:
            self._service = None

    def _invoke_with_retry(self, fn: Callable[..., T], *args: Any) -> T:
        last_exc: Optional[BaseException] = None
        for attempt in range(drive_retry_attempts()):
            try:
                with _DRIVE_API_LOCK:
                    self._ensure_service_unlocked()
                    if not self._service:
                        raise RuntimeError("Drive API unavailable")
                    return fn(*args)
            except Exception as exc:
                if not _is_transient_drive_error(exc):
                    raise
                last_exc = exc
                self._invalidate_service()
                delay = min(16.0, 0.5 * (2**attempt))
                logger.warning(
                    "Transient Drive error (attempt %s/%s), retrying in %.1fs: %s",
                    attempt + 1,
                    drive_retry_attempts(),
                    delay,
                    exc,
                )
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _ensure_service_async(self) -> None:
        if self._service is not None:
            return
        async with self._service_lock:
            if self._service is not None:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: self._invoke_with_retry(lambda: None)
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.credentials_json)

    def _ensure_real_drive(self) -> None:
        ensure_drive_configured(self.is_configured)

    def _list_files_sync(self, folder_id: str) -> List[Dict[str, Any]]:
        return self._walk_folder_sync(folder_id, "")

    def _walk_folder_sync(self, folder_id: str, relative_prefix: str) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        for item in self._list_children_sync(folder_id):
            mime = item.get("mimeType", "")
            name = item.get("name", "")
            rel = f"{relative_prefix}/{name}".strip("/") if relative_prefix else name

            if mime == "application/vnd.google-apps.folder":
                files.extend(self._walk_folder_sync(item["id"], rel))
                continue

            if not is_intake_file(name, mime):
                logger.debug("Skipping unsupported intake file %s (%s)", name, mime)
                continue

            item = dict(item)
            item["relative_path"] = rel
            files.append(item)
        return files

    def _download_file_sync(self, file_id: str) -> bytes:
        assert self._service is not None
        meta = (
            self._service.files()
            .get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True)
            .execute()
        )
        mime = meta.get("mimeType", "")

        if mime in GOOGLE_EXPORT_MIME:
            return (
                self._service.files()
                .export(fileId=file_id, mimeType=GOOGLE_EXPORT_MIME[mime], supportsAllDrives=True)
                .execute()
            )

        if mime not in SUPPORTED_BINARY_MIMES and not mime.startswith(("text/", "image/")):
            logger.warning("Attempting binary download for mime=%s file=%s", mime, meta.get("name"))

        request = self._service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def _get_metadata_sync(self, file_id: str) -> Optional[Dict[str, Any]]:
        assert self._service is not None
        try:
            return (
                self._service.files()
                .get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True)
                .execute()
            )
        except Exception as exc:
            logger.error("Drive metadata failed for %s: %s", file_id, exc)
            return None

    def _list_child_folders_sync(self, folder_id: str) -> List[Dict[str, Any]]:
        return [
            item
            for item in self._list_children_sync(folder_id)
            if item.get("mimeType") == "application/vnd.google-apps.folder"
        ]

    def _list_children_sync(self, folder_id: str) -> List[Dict[str, Any]]:
        assert self._service is not None
        query = f"'{folder_id}' in parents and trashed = false"
        items: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    pageToken=page_token,
                    pageSize=100,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    async def _run_drive_sync(self, fn, *args):
        async with _get_drive_concurrency_sem():
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: self._invoke_with_retry(fn, *args)
                ),
                timeout=drive_operation_timeout_seconds(),
            )

    async def _require_service(self) -> None:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return
            raise RuntimeError("Drive API unavailable")
        await self._ensure_service_async()
        if not self._service:
            self._ensure_real_drive()
            raise RuntimeError("Drive API unavailable")

    async def list_child_folders(self, folder_id: str) -> List[Dict[str, Any]]:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.list_child_folders(folder_id)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._list_child_folders_sync, folder_id)

    async def list_folder_children(self, folder_id: str) -> List[Dict[str, Any]]:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.list_folder_children(folder_id)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._list_children_sync, folder_id)

    async def list_files(self, folder_id: str) -> List[Dict[str, Any]]:
        return await self.list_files_recursive(folder_id)

    async def list_files_recursive(self, folder_id: str) -> List[Dict[str, Any]]:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.list_files_recursive(folder_id)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._list_files_sync, folder_id)

    async def download_file(self, file_id: str) -> bytes:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.download_file(file_id)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._download_file_sync, file_id)

    async def get_file_metadata(self, file_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.get_file_metadata(file_id)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._get_metadata_sync, file_id)

    async def handle_webhook(self, payload: Dict[str, Any]) -> List[str]:
        """Process push notification or manual trigger; return changed file IDs."""
        resource_state = payload.get("resource_state") or payload.get("X-Goog-Resource-State")
        if resource_state == "sync":
            return []

        if payload.get("changed_files"):
            return list(payload["changed_files"])

        folder_id = payload.get("folder_id") or payload.get("drive_folder_id")
        if folder_id:
            files = await self.list_files_recursive(folder_id)
            return [f["id"] for f in files]

        return []


    def _create_folder_sync(self, parent_id: str, name: str) -> str:
        assert self._service is not None
        meta = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        created = self._service.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
        return created["id"]

    def _find_file_by_name_sync(self, parent_id: str, filename: str) -> Optional[Dict[str, Any]]:
        assert self._service is not None
        escaped_name = filename.replace("'", "\\'")
        query = f"'{parent_id}' in parents and trashed = false and name = '{escaped_name}'"
        response = (
            self._service.files()
            .list(
                q=query,
                spaces="drive",
                fields="files(id, name, modifiedTime)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = response.get("files", [])
        return files[0] if files else None

    def _upload_file_sync(
        self, parent_id: str, filename: str, content: bytes, mime_type: str
    ) -> str:
        assert self._service is not None
        from googleapiclient.http import MediaIoBaseUpload

        existing = self._find_file_by_name_sync(parent_id, filename)
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
        if existing:
            updated = (
                self._service.files()
                .update(
                    fileId=existing["id"],
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute()
            )
            return updated["id"]
        meta = {"name": filename, "parents": [parent_id]}
        created = (
            self._service.files()
            .create(body=meta, media_body=media, fields="id", supportsAllDrives=True)
            .execute()
        )
        return created["id"]

    def _update_file_sync(self, file_id: str, content: bytes, mime_type: str) -> str:
        assert self._service is not None
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
        updated = (
            self._service.files()
            .update(fileId=file_id, media_body=media, supportsAllDrives=True)
            .execute()
        )
        return updated["id"]

    def _delete_file_sync(self, file_id: str) -> None:
        assert self._service is not None
        self._service.files().delete(fileId=file_id, supportsAllDrives=True).execute()

    async def warmup(self) -> None:
        if self.is_configured:
            await self._ensure_service_async()

    async def create_folder(self, parent_id: str, name: str) -> str:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.create_folder(parent_id, name)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._create_folder_sync, parent_id, name)

    async def upload_file(
        self, parent_id: str, filename: str, content: bytes, mime_type: str = "application/octet-stream"
    ) -> str:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.upload_file(parent_id, filename, content, mime_type)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(
            self._upload_file_sync, parent_id, filename, content, mime_type
        )

    async def update_file(
        self, file_id: str, content: bytes, mime_type: str = "application/octet-stream"
    ) -> str:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.update_file(file_id, content, mime_type)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        return await self._run_drive_sync(self._update_file_sync, file_id, content, mime_type)

    async def delete_file(self, file_id: str) -> None:
        if not self.is_configured:
            self._ensure_real_drive()
            if allow_mocks():
                return await self._mock.delete_file(file_id)
            raise RuntimeError("Drive API unavailable")
        await self._require_service()
        await self._run_drive_sync(self._delete_file_sync, file_id)


_DRIVE_CONNECTOR: Optional[GoogleDriveConnector] = None


def get_google_drive_connector() -> GoogleDriveConnector:
    global _DRIVE_CONNECTOR
    if _DRIVE_CONNECTOR is not None:
        return _DRIVE_CONNECTOR
    _DRIVE_CONNECTOR = GoogleDriveConnector()
    return _DRIVE_CONNECTOR


def drive_status() -> Dict[str, Any]:
    """Health/diagnostic info for /health."""
    creds = load_credentials_json()
    connector = get_google_drive_connector()
    system_root = (
        (os.getenv("GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID") or "").strip()
        or (os.getenv("GOOGLE_DRIVE_DEFAULT_FOLDER_ID") or "").strip()
        or None
    )
    if not drive_enabled():
        mode = "disabled"
    else:
        mode = "real" if connector.is_configured else ("mock" if allow_mocks() else "unconfigured")
    return {
        "configured": bool(creds),
        "mode": mode,
        "enabled": drive_enabled(),
        "real_only": not allow_mocks(),
        "system_root_folder_id": system_root,
        "system_root_name": os.getenv("GOOGLE_DRIVE_SYSTEM_ROOT_NAME", "APTP"),
        "impersonate_user": (os.getenv("GOOGLE_DRIVE_IMPERSONATE_USER") or "").strip() or None,
        "watch_enabled": drive_enabled() and os.getenv("APTP_DRIVE_WATCH_ENABLED", "false").lower() in {"1", "true", "yes"},
        "registry_enabled": os.getenv("APTP_DISABLE_INTAKE_DB", "false").lower()
        not in {"1", "true", "yes"},
        "mock_system_root": str(resolve_mock_system_root()),
    }
