from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Query

from src.services.run_access_token import redact_token, verify_run_access_token

logger = logging.getLogger(__name__)

KNOWN_WEAK_API_KEYS = frozenset(
    {
        "",
        "dev-api-key",
        "changeme",
        "secret",
        "test",
        "password",
        "aptp-dev-encryption-key",
    }
)


def aptp_env() -> str:
    return (os.getenv("APTP_ENV") or os.getenv("RAILWAY_ENVIRONMENT") or "development").strip().lower()


def is_production_env() -> bool:
    env = aptp_env()
    return env in {"production", "prod"}


def allow_open_api() -> bool:
    if is_production_env():
        return False
    return os.getenv("APTP_ALLOW_OPEN_API", "false").lower() in {"1", "true", "yes"}


def get_configured_api_key() -> str:
    return (os.getenv("APTP_API_KEY") or "").strip()


def validate_api_key_configuration() -> None:
    """Startup guard: reject empty or known-weak keys in production."""
    key = get_configured_api_key()
    if is_production_env():
        if not key or key in KNOWN_WEAK_API_KEYS:
            raise RuntimeError(
                "APTP_API_KEY must be set to a strong secret when APTP_ENV=production "
                "(known defaults like dev-api-key are blocked)."
            )
        return
    if not key and not allow_open_api():
        raise RuntimeError(
            "APTP_API_KEY is empty. Set APTP_API_KEY or APTP_ALLOW_OPEN_API=true for local dev only."
        )


def _api_key_is_valid(provided: Optional[str]) -> bool:
    expected = get_configured_api_key()
    if not expected:
        return allow_open_api()
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


async def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-Api-Key")) -> None:
    if _api_key_is_valid(x_api_key):
        return
    if not get_configured_api_key() and allow_open_api():
        return
    logger.debug("API key rejected (provided=%s)", redact_token(x_api_key))
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def verify_run_browser_access(
    run_id: str,
    token: Optional[str] = Query(None, description="Ephemeral run access token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> None:
    """Browser-facing routes: ephemeral run token or X-Api-Key (not ?api_key=)."""
    bearer = _extract_bearer_token(authorization)
    run_token = token or bearer
    if run_token and verify_run_access_token(run_token, run_id):
        return
    if run_token:
        logger.debug(
            "Run browser access denied for run_id=%s token=%s",
            run_id,
            redact_token(run_token),
        )
    if _api_key_is_valid(x_api_key):
        return
    if not get_configured_api_key() and allow_open_api():
        return
    raise HTTPException(status_code=401, detail="Invalid or missing run access credentials")


def resolve_requested_project_id(
    x_project_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[str]:
    requested = (x_project_id or project_id or "").strip()
    return requested or None


async def enforce_run_project_scope(
    run_id: str,
    *,
    x_project_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> None:
    requested = resolve_requested_project_id(x_project_id, project_id)
    if not requested:
        return
    actual = await resolve_run_project_id(run_id)
    if actual and actual != requested:
        raise HTTPException(status_code=403, detail="Run does not belong to the requested project")
    if not actual:
        raise HTTPException(status_code=404, detail="Run not found")


async def assert_run_project_scope(
    run_id: str,
    x_project_id: Optional[str] = Header(None, alias="X-Project-Id"),
    project_id: Optional[str] = Query(None, alias="project_id"),
) -> None:
    await enforce_run_project_scope(run_id, x_project_id=x_project_id, project_id=project_id)


def resolve_run_project_id_sync(run_id: str) -> Optional[str]:
    from src.memory.enhanced_memory import get_memory
    from src.services.artifact_bundle import load_run_summary
    from src.services.pipeline_checkpoint import load_resume_checkpoint

    summary = get_memory().read(f"pipeline_summary:{run_id}")
    if isinstance(summary, dict):
        project_id = summary.get("project_id")
        if project_id:
            return str(project_id)

    try:
        disk_summary = load_run_summary(run_id)
        project_id = disk_summary.get("project_id")
        if project_id:
            return str(project_id)
    except FileNotFoundError:
        pass

    checkpoint = load_resume_checkpoint(run_id)
    if isinstance(checkpoint, dict):
        project_id = checkpoint.get("project_id") or (checkpoint.get("metadata") or {}).get("project_id")
        if project_id:
            return str(project_id)
    return None


async def resolve_run_project_id(run_id: str) -> Optional[str]:
    project_id = resolve_run_project_id_sync(run_id)
    if project_id:
        return project_id
    try:
        from src.services.intake_registry import get_intake_registry

        return await get_intake_registry().get_run_project_id(run_id)
    except Exception:
        return None


async def verify_run_browser_resource(
    run_id: str,
    token: Optional[str] = Query(None, description="Ephemeral run access token"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    x_project_id: Optional[str] = Header(None, alias="X-Project-Id"),
    project_id: Optional[str] = Query(None, alias="project_id"),
) -> None:
    """Browser-facing downloads/SSE: run token or API key, optional project scope."""
    await verify_run_browser_access(run_id, token, authorization, x_api_key)
    await enforce_run_project_scope(run_id, x_project_id=x_project_id, project_id=project_id)
