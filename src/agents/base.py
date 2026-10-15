import hashlib
import json
import logging
import os
import re
import time
from typing import Optional, Dict, Any, List, Tuple

from src.exceptions import AgentResponseError
from src.agents.guardrails.agent_guardrails import redact_dict_values, redact_credentials_from_text
from src.runtime_policy import ensure_llm_configured, use_mock_llm
from src.agents.token_policy import json_for_prompt, output_tokens, prompt_budget, truncate_text
from src.agents.prompts.enterprise import ROLE_SYSTEM_PROMPTS
from src.memory.enhanced_memory import EnhancedSharedMemory, MemoryTier, get_memory
from src.agents.run_scope import bind_memory_scope, get_memory_scope
from src.knowledge.retrieval import KnowledgeRetriever, get_retriever, ContextWindow

logger = logging.getLogger("base_agent")


def _get_llm_client():
    ensure_llm_configured()
    if use_mock_llm():
        from src.services.mock_llm import MockAzureFoundryClient
        return MockAzureFoundryClient()
    from src.services.hybrid_llm import get_hybrid_llm_client

    return get_hybrid_llm_client()


class BaseAgent:
    def __init__(self, memory: Optional[EnhancedSharedMemory] = None):
        self.memory = memory or get_memory()
        self.client = _get_llm_client()
        self.retriever = get_retriever()
        self._context_window: Optional[ContextWindow] = None

    @property
    def memory_scope(self) -> Optional[str]:
        return get_memory_scope()

    def set_memory_scope(self, scope: Optional[str]) -> None:
        bind_memory_scope(scope)

    def _scoped_key(self, key: str, scope_override: Optional[str] = None) -> str:
        scope = scope_override if scope_override is not None else get_memory_scope()
        return EnhancedSharedMemory.scoped_key(scope, key)

    def read_memory(
        self,
        key: str,
        default: Any = None,
        *,
        scope_override: Optional[str] = None,
        fallback_global: bool = True,
    ) -> Any:
        scoped_key = self._scoped_key(key, scope_override=scope_override)
        value = self.memory.read_with_fallback(
            scoped_key,
            fallback_keys=[key] if fallback_global and scoped_key != key else None,
        )
        return default if value is None else value

    def write_memory(
        self,
        key: str,
        value: Any,
        *,
        tier: MemoryTier = MemoryTier.SHORT_TERM,
        tags: Optional[List[str]] = None,
    ) -> None:
        scoped_key = self._scoped_key(key)
        self.memory.write(key=scoped_key, value=value, tier=tier, tags=tags)

    @property
    def agent_name(self) -> str:
        return self.__class__.__name__

    def role_system_prompt(self, override: Optional[str] = None) -> str:
        return override or ROLE_SYSTEM_PROMPTS.get(
            self.agent_name,
            "You are an enterprise QA specialist. Respond with valid JSON when requested.",
        )

    def fit_prompt_budget(self, prompt: str, explicit_budget: Optional[int] = None) -> str:
        budget = prompt_budget(self.agent_name, len(prompt), explicit=explicit_budget)
        return truncate_text(prompt, agent_name=self.agent_name, max_chars=budget)

    def compact_json(self, data: Any, max_chars: Optional[int] = None) -> str:
        return json_for_prompt(data, agent_name=self.agent_name, max_chars=max_chars)

    @staticmethod
    def _normalize_json_text(response: str) -> str:
        text = (response or "").strip()
        if not text:
            return ""
        if text.startswith("```"):
            lines = text.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @staticmethod
    def _repair_json_text(text: str) -> str:
        """Best-effort fixes for common LLM JSON mistakes."""
        repaired = text.replace("\ufeff", "")
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return repaired

    def parse_json_response(self, response: str, fallback: Any) -> Any:
        text = self._normalize_json_text(response)
        if not text:
            return fallback
        for candidate in (text, self._repair_json_text(text)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                start, end = candidate.find("{"), candidate.rfind("}")
                if start >= 0 and end > start:
                    fragment = candidate[start : end + 1]
                    try:
                        return json.loads(fragment)
                    except json.JSONDecodeError:
                        try:
                            return json.loads(self._repair_json_text(fragment))
                        except json.JSONDecodeError:
                            pass
        return fallback

    async def ask_llm(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        expect_json: bool = True,
        prompt_budget: Optional[int] = None,
        deployment_override: Optional[str] = None,
    ) -> str:
        tuned = self.fit_prompt_budget(prompt, explicit_budget=prompt_budget)
        fingerprint = hashlib.sha256(
            f"{self.agent_name}||{system_prompt or ''}||{tuned}".encode()
        ).hexdigest()
        return await self.client.ask(
            tuned,
            system_prompt=system_prompt or self.role_system_prompt(),
            cache_key=f"llm:{fingerprint[:32]}",
            max_tokens=max_tokens or output_tokens(self.agent_name, chars_hint=len(tuned)),
            temperature=temperature if temperature is not None else 0.15,
            response_format_json=expect_json,
            metadata={"agent": self.agent_name},
            deployment_override=deployment_override,
        )

    async def ask_json(
        self,
        task_prompt: str,
        fallback: Any,
        *,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        use_fallback_on_failure: bool = False,
        max_attempts: int = 3,
    ) -> Any:
        strict_suffix = (
            "\n\nRespond with a single valid JSON object only. No markdown fences or commentary."
        )

        attempts = max(1, max_attempts)
        for attempt in range(attempts):
            prompt = task_prompt if attempt == 0 else task_prompt + strict_suffix
            temp = temperature if attempt == 0 else (temperature if temperature is not None else 0.1)
            raw = await self.ask_llm(
                prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temp,
                expect_json=True,
            )
            parsed = self.parse_json_response(raw, None)
            if parsed is not None:
                if isinstance(parsed, dict):
                    parsed.setdefault("generation_mode", "llm")
                return parsed

        if use_mock_llm() or use_fallback_on_failure:
            if use_fallback_on_failure and not use_mock_llm():
                logger.warning(
                    "%s: using deterministic fallback after %d failed JSON parse(s)",
                    self.agent_name,
                    attempts,
                )
            if isinstance(fallback, dict):
                fallback = dict(fallback)
                fallback.setdefault("generation_mode", "fallback")
            return fallback
        raise AgentResponseError(
            f"{self.agent_name} did not return valid JSON after {attempts} attempts (real-only policy)."
        )

    def store_short_term_memory(
        self, key: str, value: Any, tags: Optional[List[str]] = None
    ) -> None:
        self.write_memory(key, value, tier=MemoryTier.SHORT_TERM, tags=tags)

    def store_working_memory(self, key: str, value: Any, tags: Optional[List[str]] = None) -> None:
        self.write_memory(key, value, tier=MemoryTier.WORKING, tags=tags)

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def sanitize_for_report(self, data: Any) -> Any:
        """Prevent credentials from appearing in reports or UI payloads."""
        return redact_dict_values(data)

    def sanitize_text_for_report(self, text: str) -> str:
        return redact_credentials_from_text(text)

    # OPTIMIZATION: Semantic caching for agent results
    # Reduces LLM calls when processing similar content across runs or within the same pipeline
    _semantic_cache: Dict[str, Any] = {}

    def _compute_semantic_fingerprint(self, data: Dict[str, Any], keys: List[str]) -> str:
        """Compute a fingerprint based on specific content keys for semantic caching.

        This allows cache hits when the semantic content is identical even if
        metadata (timestamps, IDs) differs.
        """
        content_parts = []
        for key in sorted(keys):
            value = data.get(key)
            if value is None:
                continue
            if isinstance(value, (list, dict)):
                # Normalize to compact JSON for consistent hashing
                content_parts.append(f"{key}:{json.dumps(value, sort_keys=True, separators=(',', ':'))}")
            else:
                content_parts.append(f"{key}:{str(value)}")
        return hashlib.sha256("|".join(content_parts).encode()).hexdigest()

    def get_cached_result(self, cache_key: str) -> Optional[Any]:
        """Retrieve a cached result by key."""
        return self._semantic_cache.get(cache_key)

    def set_cached_result(self, cache_key: str, value: Any, ttl_seconds: int = 3600) -> None:
        """Cache a result with TTL. Uses simple timestamp-based expiration."""
        import time
        self._semantic_cache[cache_key] = {
            "value": value,
            "expires": time.time() + ttl_seconds,
        }

    def get_semantic_cached_result(
        self,
        context: Dict[str, Any],
        content_keys: List[str],
        agent_variant: str = "default",
    ) -> Optional[Any]:
        """Get cached result based on semantic content fingerprint.

        Args:
            context: The input context dict
            content_keys: Keys that represent semantic content (not metadata)
            agent_variant: Optional variant name for cache namespacing

        Returns:
            Cached result if valid, None otherwise
        """
        fingerprint = self._compute_semantic_fingerprint(context, content_keys)
        cache_key = f"semantic:{self.agent_name}:{agent_variant}:{fingerprint[:32]}"
        cached = self.get_cached_result(cache_key)
        if cached and cached.get("expires", 0) > time.time():
            logger.debug("Semantic cache hit for %s (%s)", self.agent_name, agent_variant)
            return cached["value"]
        return None

    def set_semantic_cached_result(
        self,
        context: Dict[str, Any],
        content_keys: List[str],
        value: Any,
        agent_variant: str = "default",
        ttl_seconds: int = 3600,
    ) -> None:
        """Cache result with semantic fingerprint for future deduplication."""
        fingerprint = self._compute_semantic_fingerprint(context, content_keys)
        cache_key = f"semantic:{self.agent_name}:{agent_variant}:{fingerprint[:32]}"
        self.set_cached_result(cache_key, value, ttl_seconds)
        logger.debug("Cached result for %s (%s), TTL=%ds", self.agent_name, agent_variant, ttl_seconds)
