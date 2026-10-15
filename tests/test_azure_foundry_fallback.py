from types import SimpleNamespace

import pytest

from src.services.azure_foundry import AzureFoundryClient


@pytest.fixture(autouse=True)
def _routing_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.services.ai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASONING", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_CODEGEN", "Kimi-K2.6")
    monkeypatch.setenv("APTP_HYBRID_LLM_ENABLED", "true")
    monkeypatch.setenv("APTP_LLM_CROSS_PROFILE_FALLBACK", "true")
    monkeypatch.setenv("AZURE_OPENAI_CACHE_ENABLED", "false")


def _mock_response(text: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


@pytest.mark.asyncio
async def test_ask_retries_on_alternate_deployment_after_rate_limit(monkeypatch):
    calls: list[str] = []

    def fake_create(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == "Kimi-K2.6":
            raise RuntimeError("Error code: 429 - RateLimitReached")
        return _mock_response("fallback-ok")

    client = AzureFoundryClient(deployment="gpt-4.1")
    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)

    text = await client.ask("ping", deployment_override="Kimi-K2.6")

    assert text == "fallback-ok"
    assert calls == ["Kimi-K2.6", "gpt-4.1"]


@pytest.mark.asyncio
async def test_ask_does_not_fallback_on_non_retriable_error(monkeypatch):
    calls: list[str] = []

    def fake_create(**kwargs):
        calls.append(kwargs["model"])
        raise RuntimeError("invalid request: malformed json schema")

    client = AzureFoundryClient(deployment="gpt-4.1")
    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)

    with pytest.raises(Exception, match="malformed json schema"):
        await client.ask("ping", deployment_override="Kimi-K2.6")

    assert calls == ["Kimi-K2.6"]


@pytest.mark.asyncio
async def test_ask_reasoning_failure_falls_back_to_codegen(monkeypatch):
    calls: list[str] = []

    def fake_create(**kwargs):
        model = kwargs["model"]
        calls.append(model)
        if model == "gpt-4.1":
            raise RuntimeError("Error code: 404 - Resource not found")
        return _mock_response("codegen-ok")

    client = AzureFoundryClient(deployment="gpt-4.1")
    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)

    text = await client.ask("ping", deployment_override="gpt-4.1")

    assert text == "codegen-ok"
    assert calls == ["gpt-4.1", "Kimi-K2.6"]
