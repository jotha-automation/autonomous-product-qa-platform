import logging
import os
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["health"])
logger = logging.getLogger("health")


def _azure_openai_status() -> Dict[str, Any]:
    from src.runtime_policy import allow_mocks, use_mock_llm
    from src.services.llm_routing import default_deployment

    if allow_mocks() and use_mock_llm():
        return {"status": "mock", "configured": True}

    required = [
        ("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_API_KEY")),
        ("AZURE_OPENAI_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT")),
        ("AZURE_OPENAI_DEPLOYMENT", os.getenv("AZURE_OPENAI_DEPLOYMENT")),
    ]
    missing = [name for name, value in required if not (value or "").strip()]
    if missing:
        return {"status": "missing_config", "configured": False, "missing": missing}

    deployment = default_deployment()
    try:
        from src.services.azure_foundry import AzureFoundryClient

        result = AzureFoundryClient(deployment=deployment).health_check()
        return {
            "status": result.get("status", "error"),
            "configured": True,
            "deployment": deployment,
            "endpoint_kind": result.get("endpoint_kind"),
            "api_mode": result.get("api_mode"),
            "error": result.get("error"),
        }
    except Exception as exc:
        logger.warning("Azure OpenAI health check failed: %s", exc)
        return {
            "status": "error",
            "configured": True,
            "deployment": deployment,
            "error": str(exc)[:200],
        }


def _google_drive_status() -> Dict[str, Any]:
    from src.connectors.google_drive.connector import drive_status

    return drive_status()


def _playwright_status() -> Dict[str, Any]:
    from src.playwright_version import PLAYWRIGHT_DOCKER_IMAGE
    from src.services.playwright_runtime import playwright_health_info

    info = playwright_health_info()
    return {
        "status": info.get("status", "missing"),
        "available": info.get("browsers_available", False),
        "version": info.get("version"),
        "docker_image": PLAYWRIGHT_DOCKER_IMAGE,
        "npx": bool(info.get("npx")),
        "browsers_preinstalled": info.get("browsers_preinstalled", False),
        "browsers_path": info.get("browsers_path", ""),
        "skip_install": info.get("skip_install", False),
    }


def _service_bus_status() -> Dict[str, Any]:
    conn = os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING")
    queue = os.getenv("SERVICE_BUS_QUEUE_NAME", "aptp-events")
    if not conn:
        return {
            "status": "unconfigured",
            "configured": False,
            "queue": queue,
        }
    return {
        "status": "configured",
        "configured": True,
        "queue": queue,
    }


def _redis_status() -> Dict[str, Any]:
    from src.memory.enhanced_memory import get_memory

    return get_memory().redis_health()


@router.get("/health")
async def health():
    checks = {
        "azure_openai": _azure_openai_status(),
        "google_drive": _google_drive_status(),
        "playwright": _playwright_status(),
        "service_bus": _service_bus_status(),
        "redis": _redis_status(),
    }
    all_ready = all(
        c.get("status") not in ("required_missing", "missing_config", "error")
        and (
            c.get("configured")
            or c.get("available")
            or c.get("browsers_available")
            or c.get("status") in ("mock", "unconfigured", "ok", "available", "unavailable")
        )
        for c in checks.values()
    )
    return {
        "status": "ok" if all_ready else "degraded",
        "service": "aptp",
        "checks": checks,
    }
