"""Ephemeral run access tokens (Phase 5b)."""
from __future__ import annotations

import time

import pytest

from src.services.run_access_token import (
    issue_run_access_token,
    redact_token,
    verify_run_access_token,
)


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("APTP_API_KEY", "test-signing-secret")
    monkeypatch.setenv("APTP_RUN_ACCESS_TOKEN_TTL_SECONDS", "120")


def test_issue_and_verify_run_token():
    issued = issue_run_access_token("run-abc", project_id="proj-1")
    token = issued["token"]
    assert issued["run_id"] == "run-abc"
    assert issued["expires_at"] > int(time.time())
    assert verify_run_access_token(token, "run-abc") is True


def test_verify_run_token_rejects_wrong_run_id():
    token = issue_run_access_token("run-abc")["token"]
    assert verify_run_access_token(token, "run-other") is False


def test_verify_run_token_rejects_tampered_signature():
    token = issue_run_access_token("run-abc")["token"]
    payload, _sig = token.rsplit(".", 1)
    assert verify_run_access_token(f"{payload}.deadbeef", "run-abc") is False


def test_verify_run_token_rejects_expired(monkeypatch):
    monkeypatch.setenv("APTP_RUN_ACCESS_TOKEN_TTL_SECONDS", "60")
    issued = issue_run_access_token("run-expired")
    token = issued["token"]
    monkeypatch.setattr("src.services.run_access_token.time.time", lambda: issued["expires_at"] + 1)
    assert verify_run_access_token(token, "run-expired") is False


def test_redact_token_masks_value():
    assert redact_token("abcdefghijklmnop") == "abcd…mnop"
    assert redact_token("short") == "***"
