"""Hybrid LLM routing: reasoning vs codegen deployments per agent."""
from __future__ import annotations

import os
from enum import Enum
from typing import Dict, FrozenSet, Optional


class LLMProfile(str, Enum):
    REASONING = "reasoning"
    CODEGEN = "codegen"


# Playwright TS / locator maintenance — prefer coding-oriented model (e.g. Kimi)
CODEGEN_AGENTS: FrozenSet[str] = frozenset(
    {
        "PlaywrightGeneratorAgent",
        "PlaywrightArchitectAgent",
        "SelfHealingAgent",
    }
)

# Document analysis, functional design, governance, RCA, automation planning
REASONING_AGENTS: FrozenSet[str] = frozenset(
    {
        "ReadinessAgent",
        "RequirementsAnalystAgent",
        "CoverageAgent",
        "TestDesignAgent",
        "TraceabilityAgent",
        "RiskAssessmentAgent",
        "KnowledgeRetrievalAgent",
        "ExecutiveReportingAgent",
        "FailureRCAAgent",
        "DefectDetectionAgent",
    }
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def default_deployment() -> str:
    return _env("AZURE_OPENAI_DEPLOYMENT")


def codegen_deployment() -> str:
    return _env("AZURE_OPENAI_DEPLOYMENT_CODEGEN") or default_deployment()


def reasoning_deployment() -> str:
    return _env("AZURE_OPENAI_DEPLOYMENT_REASONING") or default_deployment()


def hybrid_enabled() -> bool:
    """Use split deployments when explicitly enabled or reasoning differs from default."""
    flag = _env("APTP_HYBRID_LLM_ENABLED").lower()
    if flag in {"0", "false", "no"}:
        return False
    if flag in {"1", "true", "yes"}:
        return True
    base = default_deployment()
    return bool(
        (_env("AZURE_OPENAI_DEPLOYMENT_REASONING") and reasoning_deployment() != base)
        or (_env("AZURE_OPENAI_DEPLOYMENT_CODEGEN") and codegen_deployment() != base)
    )


def profile_for_agent(agent_name: str) -> LLMProfile:
    if agent_name in CODEGEN_AGENTS:
        return LLMProfile.CODEGEN
    return LLMProfile.REASONING


def resolve_deployment(agent_name: str) -> str:
    """Return Azure deployment name for the given agent class."""
    if not hybrid_enabled():
        return default_deployment()
    if profile_for_agent(agent_name) == LLMProfile.CODEGEN:
        return codegen_deployment()
    return reasoning_deployment()


def resolve_temperature(agent_name: str, explicit: Optional[float]) -> Optional[float]:
    if explicit is not None:
        return explicit
    if not hybrid_enabled():
        return None
    profile = profile_for_agent(agent_name)
    key = (
        "AZURE_OPENAI_TEMPERATURE_REASONING"
        if profile == LLMProfile.REASONING
        else "AZURE_OPENAI_TEMPERATURE_CODEGEN"
    )
    raw = _env(key)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def cross_profile_fallback_enabled() -> bool:
    raw = _env("APTP_LLM_CROSS_PROFILE_FALLBACK", "true").lower()
    return raw not in {"0", "false", "no"}


def fallback_deployment_for(deployment: str) -> Optional[str]:
    """
    Return the paired reasoning/codegen deployment when the primary is unavailable.
    No-op when hybrid routing is off or both profiles share the same deployment.
    """
    if not cross_profile_fallback_enabled() or not hybrid_enabled():
        return None
    dep = (deployment or "").strip()
    if not dep:
        return None
    reasoning = reasoning_deployment()
    codegen = codegen_deployment()
    if reasoning == codegen:
        return None
    if dep == codegen:
        return reasoning
    if dep == reasoning:
        return codegen
    base = default_deployment()
    if dep == base:
        if base == reasoning and codegen != reasoning:
            return codegen
        if base == codegen and reasoning != codegen:
            return reasoning
    return None


def is_deployment_fallback_error(exc: BaseException) -> bool:
    """True when retrying on the alternate reasoning/codegen deployment may succeed."""
    err = str(exc).lower()
    markers = (
        "429",
        "ratelimit",
        "rate limit",
        "too many requests",
        "resource not found",
        "deploymentnotfound",
        "model_not_found",
        "model not found",
        "unavailable",
        "temporarily unavailable",
        "service unavailable",
        " 503",
        " 502",
        " 500",
        "overloaded",
    )
    return any(marker in err for marker in markers)


def routing_summary() -> Dict[str, object]:
    """Diagnostics for /health and logs."""
    return {
        "hybrid_enabled": hybrid_enabled(),
        "cross_profile_fallback": cross_profile_fallback_enabled(),
        "default_deployment": default_deployment(),
        "reasoning_deployment": reasoning_deployment(),
        "codegen_deployment": codegen_deployment(),
        "reasoning_agents": sorted(REASONING_AGENTS),
        "codegen_agents": sorted(CODEGEN_AGENTS),
    }
