"""Load Google Drive service-account credentials from environment."""
import json
import logging
import os
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account

logger = logging.getLogger("google_drive.credentials")

SCOPES = ("https://www.googleapis.com/auth/drive",)


def drive_http_timeout_seconds() -> int:
    return max(5, int(os.getenv("GOOGLE_DRIVE_HTTP_TIMEOUT", "30")))


def load_credentials_json() -> Optional[str]:
    inline = (os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON") or "").strip()
    if inline:
        return inline

    path = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if path:
        cred_path = Path(path)
        if cred_path.is_file():
            return cred_path.read_text(encoding="utf-8")
    return None


def get_impersonate_user() -> Optional[str]:
    """Workspace domain-wide delegation: act as this user (uses their Drive quota)."""
    return (os.getenv("GOOGLE_DRIVE_IMPERSONATE_USER") or "").strip() or None


def get_service_account_credentials():
    raw = load_credentials_json()
    if not raw:
        return None
    info = json.loads(raw)
    subject = get_impersonate_user()
    return service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES, subject=subject
    )


def _auth_request_with_timeout(timeout: int):
    """Build a google-auth Request that enforces per-call HTTP timeouts."""
    import requests
    from google.auth.transport import requests as google_requests

    session = requests.Session()
    original_request = session.request

    def request_with_timeout(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original_request(method, url, **kwargs)

    session.request = request_with_timeout  # type: ignore[method-assign]
    return google_requests.Request(session=session)


def warmup_service_account_credentials(credentials) -> None:
    """Refresh OAuth token once at startup with an explicit network timeout."""
    timeout = drive_http_timeout_seconds()
    if credentials.valid:
        return
    logger.info("Refreshing Google service account token (timeout=%ss)", timeout)
    credentials.refresh(_auth_request_with_timeout(timeout))
    logger.info("Google service account token ready")

