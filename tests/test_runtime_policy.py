import pytest

from src.runtime_policy import (
    RealDataRequiredError,
    allow_mocks,
    ensure_not_mock_execution_result,
    use_mock_llm,
    use_mock_playwright_execution,
)


def test_real_only_disables_mocks_by_default(monkeypatch):
    monkeypatch.delenv("APTP_ALLOW_MOCKS", raising=False)
    monkeypatch.setenv("APTP_USE_MOCK_LLM", "true")
    monkeypatch.setenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "true")
    assert not allow_mocks()
    assert not use_mock_llm()
    assert not use_mock_playwright_execution()


def test_allow_mocks_only_when_explicit(monkeypatch):
    monkeypatch.setenv("APTP_ALLOW_MOCKS", "true")
    monkeypatch.setenv("APTP_USE_MOCK_LLM", "true")
    monkeypatch.setenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "true")
    assert allow_mocks()
    assert use_mock_llm()
    assert use_mock_playwright_execution()


def test_rejects_mock_execution_in_reports(monkeypatch):
    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    with pytest.raises(RealDataRequiredError):
        ensure_not_mock_execution_result({"mode": "mock", "passed": 1})


def test_allows_infra_error_execution_in_reports(monkeypatch):
    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    ensure_not_mock_execution_result(
        {"mode": "live", "infra_error": True, "error": "browser mismatch", "passed": 0}
    )
