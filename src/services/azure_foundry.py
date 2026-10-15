import asyncio
import hashlib
import logging
import os
import time
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

from openai import AzureOpenAI, OpenAI

logger = logging.getLogger("azure_foundry")


def normalize_azure_endpoint(endpoint: str) -> str:
    """Normalize Azure AI Foundry / Azure OpenAI endpoint for the OpenAI v1 client.

    Foundry project deployments (e.g. Kimi-K2.6) live on:
      https://<project>.services.ai.azure.com/openai/v1/

    Classic Azure OpenAI resources use:
      https://<resource>.openai.azure.com/openai/v1/
    """
    endpoint = (endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT is not set")

    # Users sometimes paste the project management URL by mistake
    if "/api/projects/" in endpoint:
        endpoint = endpoint.split("/api/projects/")[0].rstrip("/")

    if endpoint.endswith("/openai/v1"):
        return f"{endpoint}/"

    if endpoint.endswith("/openai"):
        return f"{endpoint}/v1/"

    # Foundry project / AI Services (where Kimi and other Foundry models deploy)
    if ".services.ai.azure.com" in endpoint:
        return f"{endpoint}/openai/v1/"

    # Classic Azure OpenAI resource
    if ".openai.azure.com" in endpoint:
        return f"{endpoint}/openai/v1/"

    return f"{endpoint}/openai/v1/"


def endpoint_kind(endpoint: str) -> str:
    """Return a short label for logging and diagnostics."""
    if ".services.ai.azure.com" in endpoint:
        return "foundry-project"
    if ".openai.azure.com" in endpoint:
        return "azure-openai-resource"
    return "custom"


def create_foundry_openai_client():
    """Shared OpenAI v1 client for chat and embeddings on *.services.ai.azure.com."""
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if not api_key:
        raise ValueError("AZURE_OPENAI_API_KEY is not set")
    if not endpoint:
        raise ValueError("AZURE_OPENAI_ENDPOINT is not set")
    base_url = os.getenv("AZURE_OPENAI_BASE_URL") or normalize_azure_endpoint(endpoint)
    return OpenAI(base_url=base_url, api_key=api_key)


def use_legacy_azure_client() -> bool:
    """Use AzureOpenAI only when explicitly requested."""
    return os.getenv("AZURE_OPENAI_USE_LEGACY_CLIENT", "").lower() in {
        "1",
        "true",
        "yes",
    }


class AzureFoundryClient:
    """Azure AI Foundry client using the OpenAI v1 data plane.

    Configure for Kimi / Foundry NextGen:
      AZURE_OPENAI_ENDPOINT=https://infrawise-dev.services.ai.azure.com
      AZURE_OPENAI_DEPLOYMENT=Kimi-K2.6
      AZURE_OPENAI_API_KEY=<key from Foundry project>

    See: https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle
    """

    _response_cache: Dict[str, Tuple[float, str]] = {}
    _inflight_requests: Dict[str, asyncio.Future] = {}
    _cache_lock: Optional[asyncio.Lock] = None
    _semaphore: Optional[asyncio.Semaphore] = None
    _usage_totals: Dict[str, int] = {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_hits": 0,
    }
    _usage_by_deployment: Dict[str, Dict[str, int]] = {}

    def __init__(self, *, deployment: Optional[str] = None):
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.deployment = (deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self._legacy = use_legacy_azure_client()

        if not self.api_key:
            raise ValueError("AZURE_OPENAI_API_KEY is not set")
        if not self.deployment:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT is not set")
        if not self.endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT is not set")

        self._endpoint_kind = endpoint_kind(self.endpoint)

        if self._legacy:
            self.client = AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.endpoint.rstrip("/"),
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
            self._api_mode = "legacy"
            self.base_url = self.endpoint
        else:
            # Allow exact override (e.g. https://infrawise-dev.services.ai.azure.com/openai/v1)
            self.base_url = os.getenv("AZURE_OPENAI_BASE_URL") or normalize_azure_endpoint(
                self.endpoint
            )
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
            )
            self._api_mode = "foundry-v1"

        if AzureFoundryClient._cache_lock is None:
            AzureFoundryClient._cache_lock = asyncio.Lock()
        if AzureFoundryClient._semaphore is None:
            max_concurrency = max(1, int(os.getenv("AZURE_OPENAI_MAX_CONCURRENCY", "4")))
            AzureFoundryClient._semaphore = asyncio.Semaphore(max_concurrency)

        host = urlparse(self.base_url).netloc or self.base_url
        logger.info(
            "Azure client ready mode=%s kind=%s host=%s deployment=%s",
            self._api_mode,
            self._endpoint_kind,
            host,
            self.deployment,
        )

        if self._endpoint_kind == "azure-openai-resource" and self.deployment:
            logger.warning(
                "Endpoint is *.openai.azure.com but Foundry models (e.g. Kimi-K2.6) "
                "are usually deployed on *.services.ai.azure.com — use the project "
                "target URI + /openai/v1 or you may get 404 Resource not found."
            )

    @staticmethod
    def _cache_enabled() -> bool:
        return os.getenv("AZURE_OPENAI_CACHE_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
        }

    @staticmethod
    def _cache_ttl_seconds() -> int:
        return int(os.getenv("AZURE_OPENAI_CACHE_TTL_SECONDS", "900"))

    def _build_cache_key(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
        response_format_json: bool,
        metadata: Optional[Dict[str, Any]],
        deployment: str,
    ) -> str:
        agent = (metadata or {}).get("agent", "unknown")
        payload = "||".join(
            [
                deployment,
                system_prompt,
                prompt,
                str(max_tokens),
                str(temperature),
                "json" if response_format_json else "text",
                str(agent),
            ]
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"aoai:{digest[:32]}"

    async def _cache_get(self, key: str, ttl_seconds: int) -> Optional[str]:
        if not self._cache_enabled():
            return None

        async with AzureFoundryClient._cache_lock:
            cached = AzureFoundryClient._response_cache.get(key)
            if not cached:
                return None
            created_at, content = cached
            if time.time() - created_at > ttl_seconds:
                AzureFoundryClient._response_cache.pop(key, None)
                return None
            AzureFoundryClient._usage_totals["cache_hits"] += 1
            return content

    async def _cache_set(self, key: str, content: str) -> None:
        if not self._cache_enabled():
            return
        async with AzureFoundryClient._cache_lock:
            AzureFoundryClient._response_cache[key] = (time.time(), content)
            max_entries = max(100, int(os.getenv("AZURE_OPENAI_CACHE_MAX_ENTRIES", "1024")))
            if len(AzureFoundryClient._response_cache) > max_entries:
                # Remove oldest ~10% to keep operations cheap while bounded.
                eviction_count = max(1, max_entries // 10)
                oldest_keys = sorted(
                    AzureFoundryClient._response_cache.items(),
                    key=lambda item: item[1][0],
                )[:eviction_count]
                for old_key, _ in oldest_keys:
                    AzureFoundryClient._response_cache.pop(old_key, None)

    @staticmethod
    def _usage_to_dict(usage: Any) -> Dict[str, int]:
        if usage is None:
            return {}
        if isinstance(usage, dict):
            return {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    @classmethod
    def get_usage_totals(cls) -> Dict[str, int]:
        """Return aggregate usage counters for observability/debugging."""
        return dict(cls._usage_totals)

    @classmethod
    def get_usage_by_deployment(cls) -> Dict[str, Dict[str, int]]:
        return {dep: dict(stats) for dep, stats in cls._usage_by_deployment.items()}

    @classmethod
    def _record_usage(cls, deployment: str, usage: Dict[str, int]) -> None:
        bucket = cls._usage_by_deployment.setdefault(
            deployment,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += usage.get("prompt_tokens", 0)
        bucket["completion_tokens"] += usage.get("completion_tokens", 0)
        bucket["total_tokens"] += usage.get("total_tokens", 0)

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
        """Send a prompt to the deployed model and return text content."""
        loop = asyncio.get_running_loop()
        effective_deployment = (deployment_override or self.deployment).strip()
        system_content = system_prompt or (
            "You are an AI QA agent. Respond with valid JSON when the user requests JSON."
        )
        effective_max_tokens = max_tokens or int(
            os.getenv("APTP_DEFAULT_MAX_OUTPUT_TOKENS", "16384")
        )
        effective_temperature = (
            temperature
            if temperature is not None
            else float(os.getenv("AZURE_OPENAI_TEMPERATURE", "0.2"))
        )
        ttl_seconds = cache_ttl_seconds or self._cache_ttl_seconds()
        resolved_cache_key = cache_key or self._build_cache_key(
            prompt=prompt,
            system_prompt=system_content,
            max_tokens=effective_max_tokens,
            temperature=effective_temperature,
            response_format_json=response_format_json,
            metadata=metadata,
            deployment=effective_deployment,
        )

        cached = await self._cache_get(resolved_cache_key, ttl_seconds)
        if cached is not None:
            return cached

        # De-duplicate in-flight identical requests to avoid duplicate token spend.
        async with AzureFoundryClient._cache_lock:
            in_flight = AzureFoundryClient._inflight_requests.get(resolved_cache_key)
            if in_flight is None:
                in_flight = loop.create_future()
                AzureFoundryClient._inflight_requests[resolved_cache_key] = in_flight
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            return await in_flight

        from src.services.llm_routing import fallback_deployment_for, is_deployment_fallback_error

        primary_deployment = effective_deployment
        fallback_deployment = fallback_deployment_for(primary_deployment)
        deployments_to_try = [primary_deployment]
        if fallback_deployment and fallback_deployment not in deployments_to_try:
            deployments_to_try.append(fallback_deployment)

        def _is_transient_openai_error(exc: Exception) -> bool:
            err_text = str(exc).lower()
            return any(
                token in err_text
                for token in (
                    "rate limit",
                    "timeout",
                    "connection",
                    "internal server",
                    "503",
                    "429",
                    "500",
                    "502",
                    "504",
                    "temporarily unavailable",
                )
            ) or any(
                token in exc.__class__.__name__.lower()
                for token in ("ratelimit", "timeout", "connection", "internalserver")
            )

        async def _complete_on(deployment: str, json_mode: bool, max_tok: int):
            def _call_openai(use_json: bool, tokens: int):
                request_kwargs: Dict[str, Any] = {
                    "model": deployment,
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": effective_temperature,
                    "max_tokens": tokens,
                }
                if use_json:
                    request_kwargs["response_format"] = {"type": "json_object"}
                logger.info(
                    "Calling Azure deployment=%s base_url=%s prompt_chars=%d max_tokens=%d json_mode=%s",
                    deployment,
                    self.base_url,
                    len(prompt),
                    tokens,
                    use_json,
                )
                return self.client.chat.completions.create(**request_kwargs)

            current_max_tokens = max_tok
            last_err: Exception | None = None
            for attempt in range(2):
                try:
                    try:
                        return await loop.run_in_executor(
                            None, _call_openai, json_mode, current_max_tokens
                        )
                    except Exception as json_err:
                        if json_mode and "response_format" in str(json_err).lower():
                            logger.warning(
                                "JSON response format unsupported by deployment=%s; retrying without JSON mode",
                                deployment,
                            )
                            return await loop.run_in_executor(
                                None, _call_openai, False, current_max_tokens
                            )
                        raise
                except Exception as exc:
                    last_err = exc
                    err_text = str(exc).lower()
                    if (
                        attempt == 0
                        and "max_tokens is too large" in err_text
                        and current_max_tokens > 8_192
                    ):
                        current_max_tokens = max(8_192, current_max_tokens // 2)
                        logger.warning(
                            "Reducing max_tokens to %d for deployment=%s after provider rejection",
                            current_max_tokens,
                            deployment,
                        )
                        continue
                    raise
            if last_err is not None:
                raise last_err
            raise RuntimeError("Azure completion failed without exception")

        max_retries_outer = max(1, int(os.getenv("AZURE_OPENAI_MAX_RETRIES", "3")))
        retry_delay_base = float(os.getenv("AZURE_OPENAI_RETRY_DELAY_SECONDS", "1.0"))

        try:
            async with AzureFoundryClient._semaphore:
                response = None
                used_deployment = primary_deployment
                last_exc: Exception | None = None
                for outer_attempt in range(max_retries_outer):
                    try:
                        for dep_index, deployment in enumerate(deployments_to_try):
                            try:
                                response = await _complete_on(
                                    deployment, response_format_json, effective_max_tokens
                                )
                                used_deployment = deployment
                                if dep_index > 0:
                                    logger.warning(
                                        "Azure completion succeeded on fallback deployment=%s (primary=%s)",
                                        deployment,
                                        primary_deployment,
                                    )
                                break
                            except Exception as exc:
                                last_exc = exc
                                can_fallback = (
                                    dep_index == 0
                                    and len(deployments_to_try) > 1
                                    and is_deployment_fallback_error(exc)
                                )
                                if can_fallback:
                                    logger.warning(
                                        "Deployment %s failed (%s); retrying with alternate deployment=%s",
                                        deployment,
                                        exc,
                                        deployments_to_try[1],
                                    )
                                    continue
                                raise
                        break
                    except Exception as exc:
                        last_exc = exc
                        if not _is_transient_openai_error(exc) or outer_attempt >= max_retries_outer - 1:
                            raise
                        delay = retry_delay_base * (2 ** outer_attempt)
                        logger.warning(
                            "Transient Azure OpenAI error (attempt %d/%d), retrying in %.1fs: %s",
                            outer_attempt + 1,
                            max_retries_outer,
                            delay,
                            exc,
                        )
                        async with AzureFoundryClient._cache_lock:
                            existing = AzureFoundryClient._inflight_requests.get(resolved_cache_key)
                            if existing is in_flight:
                                AzureFoundryClient._inflight_requests.pop(resolved_cache_key, None)
                        await asyncio.sleep(delay)
                if response is None:
                    if last_exc is not None:
                        raise last_exc
                    raise RuntimeError("Azure completion returned no response")

            content = response.choices[0].message.content or ""
            usage = self._usage_to_dict(getattr(response, "usage", None))
            AzureFoundryClient._usage_totals["calls"] += 1
            AzureFoundryClient._usage_totals["prompt_tokens"] += usage.get("prompt_tokens", 0)
            AzureFoundryClient._usage_totals["completion_tokens"] += usage.get("completion_tokens", 0)
            AzureFoundryClient._usage_totals["total_tokens"] += usage.get("total_tokens", 0)
            AzureFoundryClient._record_usage(used_deployment, usage)

            logger.info(
                "Azure completion ok deployment=%s usage=%s metadata=%s",
                used_deployment,
                usage,
                metadata or {},
            )

            await self._cache_set(resolved_cache_key, content)
            in_flight.set_result(content)
            return content
        except Exception as e:
            if not in_flight.done():
                in_flight.set_exception(e)
            hint = ""
            err = str(e)
            if "404" in err and "Resource not found" in err:
                hint = (
                    f" Hint: Deployment '{primary_deployment}' was not found. "
                    "In Azure AI Foundry → Deployments, copy the exact deployment name "
                    "(e.g. gpt-4.1 vs gpt-4.1-mini, Kimi-K2.6). "
                    "Hybrid routing uses AZURE_OPENAI_DEPLOYMENT_REASONING for Knowledge/Requirements/etc. "
                    "Endpoint must be https://<project>.services.ai.azure.com for Foundry models."
                )
            logger.error("Azure API call failed: %s%s", e, hint)
            raise Exception(f"Azure OpenAI API call failed: {e}{hint}") from e
        finally:
            async with AzureFoundryClient._cache_lock:
                existing = AzureFoundryClient._inflight_requests.get(resolved_cache_key)
                if existing is in_flight:
                    AzureFoundryClient._inflight_requests.pop(resolved_cache_key, None)

    async def health_check(self) -> dict:
        """Verify connectivity with a minimal completion request."""
        try:
            text = await self.ask("Reply with exactly: OK", system_prompt="Reply briefly.")
            return {
                "status": "ok",
                "api_mode": self._api_mode,
                "endpoint_kind": self._endpoint_kind,
                "base_url": self.base_url,
                "deployment": self.deployment,
                "sample": (text or "")[:50],
            }
        except Exception as e:
            return {
                "status": "error",
                "api_mode": self._api_mode,
                "endpoint_kind": self._endpoint_kind,
                "base_url": self.base_url,
                "deployment": self.deployment,
                "error": str(e),
            }
