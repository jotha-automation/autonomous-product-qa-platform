"""Hybrid Azure LLM client — routes agents to reasoning vs codegen deployments."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from src.services.azure_foundry import AzureFoundryClient
from src.services.llm_routing import (
    hybrid_enabled,
    profile_for_agent,
    resolve_deployment,
    resolve_temperature,
    routing_summary,
)

logger = logging.getLogger("hybrid_llm")


class HybridAzureLLMClient:
    """Drop-in replacement for AzureFoundryClient with per-agent deployment routing."""

    def __init__(self) -> None:
        self._delegate = AzureFoundryClient()
        if hybrid_enabled():
            logger.info("Hybrid LLM routing enabled: %s", routing_summary())

    async def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        cache_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        response_format_json: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        cache_ttl_seconds: Optional[int] = None,
        deployment_override: Optional[str] = None,
    ) -> str:
        agent = (metadata or {}).get("agent", "")
        deployment = deployment_override or resolve_deployment(agent)
        effective_temp = resolve_temperature(agent, temperature)
        meta = dict(metadata or {})
        meta["llm_profile"] = profile_for_agent(agent).value
        meta["deployment"] = deployment
        return await self._delegate.ask(
            prompt,
            system_prompt,
            cache_key=cache_key,
            max_tokens=max_tokens,
            temperature=effective_temp,
            response_format_json=response_format_json,
            metadata=meta,
            cache_ttl_seconds=cache_ttl_seconds,
            deployment_override=deployment,
        )

    async def health_check(self) -> dict:
        if not hybrid_enabled():
            base = await self._delegate.health_check()
            base["hybrid"] = False
            return base

        from src.services.llm_routing import codegen_deployment, reasoning_deployment

        profiles: Dict[str, dict] = {}
        overall = "ok"
        for label, dep in (
            ("reasoning", reasoning_deployment()),
            ("codegen", codegen_deployment()),
        ):
            try:
                probe = AzureFoundryClient(deployment=dep)
                profiles[label] = await probe.health_check()
                if profiles[label].get("status") != "ok":
                    overall = "degraded"
            except Exception as exc:
                profiles[label] = {"status": "error", "deployment": dep, "error": str(exc)}
                overall = "error"

        return {
            "status": overall,
            "hybrid": True,
            "routing": routing_summary(),
            "profiles": profiles,
        }

    @classmethod
    def get_usage_totals(cls) -> Dict[str, int]:
        return AzureFoundryClient.get_usage_totals()

    @classmethod
    def get_usage_by_deployment(cls) -> Dict[str, Dict[str, int]]:
        return AzureFoundryClient.get_usage_by_deployment()


@lru_cache
def get_hybrid_llm_client() -> HybridAzureLLMClient:
    return HybridAzureLLMClient()
