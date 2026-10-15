"""Enforce real AI / Drive / Playwright data — mocks only when APTP_ALLOW_MOCKS=true (CI)."""
from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("runtime_policy")


class RealDataRequiredError(RuntimeError):
    """Raised when mock or synthetic data would be used but APTP_ALLOW_MOCKS is disabled."""


class RedisRequiredError(RuntimeError):
    """Raised when APTP_REQUIRE_REDIS=true but Redis is unreachable."""


def allow_mocks() -> bool:
    return os.getenv("APTP_ALLOW_MOCKS", "false").lower() in {"1", "true", "yes"}


def require_redis() -> bool:
    return os.getenv("APTP_REQUIRE_REDIS", "false").lower() in {"1", "true", "yes"}


def ensure_redis_available(memory: Any) -> None:
    """Fail startup when Redis is mandatory but the memory backend fell back to in-process dict."""
    if require_redis() and not memory.is_redis_available():
        raise RedisRequiredError(
            "APTP_REQUIRE_REDIS=true but Redis is unavailable. "
            "Check REDIS_URL and that the Redis service is reachable."
        )


def use_mock_llm() -> bool:
    if not allow_mocks():
        return False
    return os.getenv("APTP_USE_MOCK_LLM", "false").lower() in {"1", "true", "yes"}


def use_mock_playwright_execution() -> bool:
    if not allow_mocks():
        return False
    return os.getenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "false").lower() in {"1", "true", "yes"}


def skip_playwright_install() -> bool:
    """Skip per-run browser install when browsers are pre-installed (Docker image) or mocks are on."""
    if allow_mocks() and use_mock_playwright_execution():
        return True
    return os.getenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "false").lower() in {"1", "true", "yes"}


def data_provenance() -> Dict[str, str]:
    return {
        "policy": "mock" if allow_mocks() else "real_only",
        "llm": "mock" if use_mock_llm() else "azure_openai",
        "drive": "mock" if allow_mocks() else "google_drive",
        "execution": "mock" if use_mock_playwright_execution() else "playwright_live",
        "embeddings": "hash_local" if use_mock_llm() else "azure_openai",
    }


def ensure_llm_configured() -> None:
    if use_mock_llm():
        return
    missing = [
        name
        for name, val in (
            ("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_API_KEY")),
            ("AZURE_OPENAI_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT")),
            ("AZURE_OPENAI_DEPLOYMENT", os.getenv("AZURE_OPENAI_DEPLOYMENT")),
        )
        if not (val or "").strip()
    ]
    if missing:
        raise RealDataRequiredError(
            f"Azure OpenAI is required (set {', '.join(missing)}). "
            "Mocks are disabled (APTP_ALLOW_MOCKS=false)."
        )


def ensure_drive_configured(is_configured: bool) -> None:
    if allow_mocks() or is_configured:
        return
    raise RealDataRequiredError(
        "Google Drive is not configured. Set GOOGLE_DRIVE_CREDENTIALS_JSON and "
        "GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID. Mocks are disabled (APTP_ALLOW_MOCKS=false)."
    )


def ensure_playwright_live() -> None:
    if use_mock_playwright_execution():
        return
    if not shutil.which("npx") and not shutil.which("npm"):
        raise RealDataRequiredError(
            "Live Playwright requires npx/npm in PATH. Set APTP_MOCK_PLAYWRIGHT_EXECUTION=false "
            "and use the Playwright Docker image, or enable mocks only in CI (APTP_ALLOW_MOCKS=true)."
        )
    from src.services.playwright_runtime import ensure_playwright_browsers

    ensure_playwright_browsers()


def ensure_embeddings_configured() -> None:
    if use_mock_llm():
        return
    deployment = (os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "").strip()
    if not deployment:
        raise RealDataRequiredError(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT is required when using real LLM mode "
            "(e.g. text-embedding-3-small)."
        )


def ensure_not_mock_execution_result(result: Optional[Dict[str, Any]]) -> None:
    if allow_mocks() or not result:
        return
    if result.get("infra_error"):
        return
    if (result.get("mode") or "").lower() == "mock":
        raise RealDataRequiredError(
            "Execution result used mock mode; refusing to publish. "
            "Set APTP_MOCK_PLAYWRIGHT_EXECUTION=false for real E2E runs."
        )


async def _probe_chat_deployment(label: str, deployment: str) -> Optional[str]:
    if not deployment:
        return f"{label}: deployment name is empty"
    from src.services.azure_foundry import AzureFoundryClient

    result = await AzureFoundryClient(deployment=deployment).health_check()
    if result.get("status") == "ok":
        return None
    err = result.get("error") or result
    return (
        f"{label}: deployment '{deployment}' is not reachable ({err}). "
        "Use the exact deployment name from Azure AI Foundry (Deployments)."
    )


_startup_validation_cache: Optional[Dict[str, Any]] = None
_startup_validation_cached_at: float = 0.0


def _startup_validation_ttl_seconds() -> float:
    return float(os.getenv("APTP_STARTUP_VALIDATION_TTL_SECONDS", "300"))


async def validate_startup_configuration() -> Dict[str, Any]:
    """Called at API startup — probes every Azure deployment before accepting traffic.

    Results are cached per process so repeated runs do not block on the same probes.
    """
    global _startup_validation_cache, _startup_validation_cached_at
    now = time.time()
    ttl = _startup_validation_ttl_seconds()
    if _startup_validation_cache is not None and (now - _startup_validation_cached_at) < ttl:
        return _startup_validation_cache

    from src.api.deps import validate_api_key_configuration

    validate_api_key_configuration()

    prov = data_provenance()
    if allow_mocks():
        logger.warning("APTP_ALLOW_MOCKS=true — mock LLM/Drive/execution permitted (CI/dev only)")
        return {"ok": True, "provenance": prov, "warnings": ["mocks_allowed"]}

    ensure_llm_configured()
    ensure_embeddings_configured()
    ensure_playwright_live()
    from src.connectors.google_drive.connector import get_google_drive_connector
    from src.services.llm_routing import (
        codegen_deployment,
        default_deployment,
        hybrid_enabled,
        reasoning_deployment,
    )

    connector = get_google_drive_connector()
    ensure_drive_configured(connector.is_configured)

    probe_errors: list[str] = []
    probes: list[tuple[str, str]] = [("default", default_deployment())]
    if hybrid_enabled():
        probes.extend(
            [
                ("reasoning", reasoning_deployment()),
                ("codegen", codegen_deployment()),
            ]
        )
    for label, dep in probes:
        err = await _probe_chat_deployment(label, dep)
        if err:
            probe_errors.append(err)

    emb_dep = (os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "").strip()
    if emb_dep:
        try:
            from src.knowledge.embeddings import embed_text

            await embed_text("aptp startup embedding probe")
        except Exception as exc:
            probe_errors.append(
                f"embedding: deployment '{emb_dep}' failed ({exc}). "
                "Deploy text-embedding-3-small (or your embedding model) in Foundry with that exact name."
            )

    if probe_errors:
        raise RealDataRequiredError(
            "Azure deployment validation failed:\n- " + "\n- ".join(probe_errors)
        )

    logger.info("Real-only runtime policy validated: %s", prov)
    _startup_validation_cache = {"ok": True, "provenance": prov, "deployments_probed": [p[0] for p in probes]}
    _startup_validation_cached_at = time.time()
    return _startup_validation_cache
