import os

import pytest

from src.services.llm_routing import (
    CODEGEN_AGENTS,
    REASONING_AGENTS,
    fallback_deployment_for,
    hybrid_enabled,
    is_deployment_fallback_error,
    profile_for_agent,
    resolve_deployment,
)


@pytest.fixture(autouse=True)
def _clear_routing_env(monkeypatch):
    for key in (
        "APTP_HYBRID_LLM_ENABLED",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT_REASONING",
        "AZURE_OPENAI_DEPLOYMENT_CODEGEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_hybrid_disabled_without_reasoning_deployment(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "Kimi-K2.6")
    assert not hybrid_enabled()
    assert resolve_deployment("TestDesignAgent") == "Kimi-K2.6"


def test_hybrid_routes_agents(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "Kimi-K2.6")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASONING", "gpt-4.1")
    monkeypatch.setenv("APTP_HYBRID_LLM_ENABLED", "true")

    assert hybrid_enabled()
    assert resolve_deployment("TestDesignAgent") == "gpt-4.1"
    assert resolve_deployment("RequirementsAnalystAgent") == "gpt-4.1"
    assert resolve_deployment("PlaywrightGeneratorAgent") == "Kimi-K2.6"
    assert resolve_deployment("SelfHealingAgent") == "Kimi-K2.6"


def test_codegen_override(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASONING", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_CODEGEN", "Kimi-K2.6")
    monkeypatch.setenv("APTP_HYBRID_LLM_ENABLED", "true")

    assert resolve_deployment("PlaywrightGeneratorAgent") == "Kimi-K2.6"


def test_hybrid_enabled_when_only_codegen_differs(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_CODEGEN", "Kimi-K2.6")
    monkeypatch.delenv("APTP_HYBRID_LLM_ENABLED", raising=False)

    assert hybrid_enabled()
    assert resolve_deployment("PlaywrightGeneratorAgent") == "Kimi-K2.6"
    assert resolve_deployment("TestDesignAgent") == "gpt-4.1"


def test_agent_coverage():
    assert "PlaywrightGeneratorAgent" in CODEGEN_AGENTS
    assert "PlaywrightArchitectAgent" in CODEGEN_AGENTS
    assert "TestDesignAgent" in REASONING_AGENTS
    assert profile_for_agent("PlaywrightGeneratorAgent").value == "codegen"
    assert profile_for_agent("PlaywrightArchitectAgent").value == "codegen"
    assert profile_for_agent("TraceabilityAgent").value == "reasoning"


def test_fallback_deployment_pairs(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASONING", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_CODEGEN", "Kimi-K2.6")
    monkeypatch.setenv("APTP_HYBRID_LLM_ENABLED", "true")

    assert fallback_deployment_for("Kimi-K2.6") == "gpt-4.1"
    assert fallback_deployment_for("gpt-4.1") == "Kimi-K2.6"


def test_no_fallback_when_same_deployment(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASONING", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_CODEGEN", "gpt-4.1")
    monkeypatch.setenv("APTP_HYBRID_LLM_ENABLED", "true")

    assert fallback_deployment_for("gpt-4.1") is None


def test_fallback_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASONING", "gpt-4.1")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_CODEGEN", "Kimi-K2.6")
    monkeypatch.setenv("APTP_HYBRID_LLM_ENABLED", "true")
    monkeypatch.setenv("APTP_LLM_CROSS_PROFILE_FALLBACK", "false")

    assert fallback_deployment_for("Kimi-K2.6") is None


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 429 - RateLimitReached",
        "DeploymentNotFound: Resource not found",
        "model temporarily unavailable",
        "HTTP 503 Service Unavailable",
    ],
)
def test_is_deployment_fallback_error(message):
    assert is_deployment_fallback_error(RuntimeError(message))


def test_is_not_deployment_fallback_error():
    assert not is_deployment_fallback_error(RuntimeError("invalid json in response"))
