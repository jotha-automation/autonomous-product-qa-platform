"""Map Google Drive API errors to HTTP responses."""
from __future__ import annotations

import asyncio
import socket
import ssl

from fastapi import HTTPException
from googleapiclient.errors import HttpError


def http_exception_from_drive_error(exc: HttpError) -> HTTPException:
    status = exc.resp.status if exc.resp else 502
    reason = str(exc)

    if status == 403:
        if "storageQuotaExceeded" in reason or "do not have storage quota" in reason:
            return HTTPException(
                status_code=403,
                detail=(
                    "Google Drive: service accounts cannot upload to personal My Drive (no storage quota). "
                    "Use one of: (1) Move APTP/ to a Google Workspace Shared Drive and grant the service "
                    "account Content manager on that Shared Drive; (2) Enable domain-wide delegation and set "
                    "GOOGLE_DRIVE_IMPERSONATE_USER to your Workspace user email; "
                    "(3) Upload project folders manually in Drive and use APTP to process them."
                ),
            )
        if "insufficientParentPermissions" in reason or "insufficientFilePermissions" in reason:
            return HTTPException(
                status_code=403,
                detail=(
                    "Google Drive write access denied. Share the APTP system root folder "
                    "with your service account email as Editor (Content manager), not Viewer."
                ),
            )
        return HTTPException(
            status_code=403,
            detail="Google Drive permission denied. Verify the service account has Editor access to the APTP folder.",
        )

    if status == 404:
        return HTTPException(
            status_code=404,
            detail="Google Drive folder not found. Check GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID.",
        )

    return HTTPException(status_code=502, detail=f"Google Drive API error ({status}): {reason}")


def message_from_drive_error(exc: BaseException) -> str:
    """Human-readable message for logs, SSE, or UI (no HTTPException)."""
    if isinstance(exc, HttpError):
        detail = http_exception_from_drive_error(exc).detail
        return detail if isinstance(detail, str) else str(detail)
    return str(exc)


def is_storage_quota_error(exc: BaseException) -> bool:
    """True when a service account tried to write to personal My Drive (no quota)."""
    reason = str(exc)
    return "storageQuotaExceeded" in reason or "do not have storage quota" in reason.lower()


def http_exception_from_network_error(exc: BaseException) -> HTTPException:
    ssl_hint = ""
    if isinstance(exc, ssl.SSLError) or "ssl" in str(exc).lower():
        ssl_hint = (
            " Transient SSL errors often clear on retry (VPN/proxy or concurrent Drive calls). "
            "Reduce parallel UI requests or increase GOOGLE_DRIVE_RETRY_ATTEMPTS."
        )
    return HTTPException(
        status_code=503,
        detail=(
            "Google Drive is unreachable from the API (network/SSL). "
            "Ensure the container can reach oauth2.googleapis.com and www.googleapis.com. "
            "Check VPN/firewall/DNS, increase GOOGLE_DRIVE_HTTP_TIMEOUT, or retry."
            f"{ssl_hint} Cause: {exc}"
        ),
    )


def raise_for_drive_error(exc: BaseException) -> None:
    if isinstance(exc, HttpError):
        raise http_exception_from_drive_error(exc) from exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, socket.timeout, ssl.SSLError)):
        raise http_exception_from_network_error(exc) from exc
    lowered = str(exc).lower()
    if "timed out" in lowered or "ssl" in lowered:
        raise http_exception_from_network_error(exc) from exc
    raise exc
