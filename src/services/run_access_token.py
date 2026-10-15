"""Short-lived HMAC-signed tokens for browser-facing run resources (SSE, downloads)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600


def _token_ttl_seconds() -> int:
    raw = (os.getenv("APTP_RUN_ACCESS_TOKEN_TTL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _signing_secret() -> bytes:
    secret = (
        (os.getenv("APTP_RUN_ACCESS_TOKEN_SECRET") or "").strip()
        or (os.getenv("APTP_API_KEY") or "").strip()
    )
    if not secret:
        allow_open = os.getenv("APTP_ALLOW_OPEN_API", "false").lower() in {"1", "true", "yes"}
        if allow_open:
            secret = "aptp-local-run-token-secret"
        else:
            raise ValueError(
                "APTP_RUN_ACCESS_TOKEN_SECRET or APTP_API_KEY required to issue run access tokens"
            )
    return secret.encode("utf-8")


def redact_token(value: Optional[str]) -> str:
    """Redact bearer/query tokens for logs."""
    if not value:
        return "<empty>"
    text = str(value).strip()
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}…{text[-4:]}"


def issue_run_access_token(run_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    expires_at = int(time.time()) + _token_ttl_seconds()
    body: Dict[str, Any] = {"run_id": run_id, "exp": expires_at}
    if project_id:
        body["project_id"] = project_id
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    signature = hmac.new(_signing_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    token = f"{payload_b64}.{signature}"
    logger.debug("Issued run access token for run_id=%s expires_at=%s", run_id, expires_at)
    return {"token": token, "expires_at": expires_at, "run_id": run_id}


def _decode_payload(payload_b64: str) -> Optional[Dict[str, Any]]:
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        body = json.loads(raw.decode("utf-8"))
        return body if isinstance(body, dict) else None
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def verify_run_access_token(token: str, run_id: str) -> bool:
    if not token or not run_id:
        return False
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        logger.debug("Invalid run token format for run_id=%s token=%s", run_id, redact_token(token))
        return False
    payload_b64, signature = parts
    try:
        expected = hmac.new(_signing_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    except ValueError:
        return False
    if not hmac.compare_digest(signature, expected):
        logger.debug("Run token signature mismatch for run_id=%s token=%s", run_id, redact_token(token))
        return False
    body = _decode_payload(payload_b64)
    if not body:
        return False
    if body.get("run_id") != run_id:
        return False
    if int(body.get("exp") or 0) < int(time.time()):
        logger.debug("Run token expired for run_id=%s", run_id)
        return False
    return True


def project_id_from_token(token: str) -> Optional[str]:
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    body = _decode_payload(parts[0])
    if not body:
        return None
    project_id = body.get("project_id")
    return str(project_id) if project_id else None
