"""API key auth hardening (Phase 5a)."""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from src.api.deps import (
    allow_open_api,
    get_configured_api_key,
    validate_api_key_configuration,
    verify_api_key,
)


@pytest.mark.asyncio
async def test_verify_api_key_rejects_missing_when_key_configured(monkeypatch):
    monkeypatch.setenv("APTP_API_KEY", "super-secret-key")
    monkeypatch.delenv("APTP_ALLOW_OPEN_API", raising=False)
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_accepts_valid_header(monkeypatch):
    monkeypatch.setenv("APTP_API_KEY", "super-secret-key")
    monkeypatch.delenv("APTP_ALLOW_OPEN_API", raising=False)
    await verify_api_key("super-secret-key")


@pytest.mark.asyncio
async def test_verify_api_key_rejects_wrong_key_with_compare_digest(monkeypatch):
    monkeypatch.setenv("APTP_API_KEY", "a" * 32)
    monkeypatch.delenv("APTP_ALLOW_OPEN_API", raising=False)
    with pytest.raises(HTTPException) as exc:
        await verify_api_key("b" * 32)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_allows_open_api_only_when_flag_set(monkeypatch):
    monkeypatch.delenv("APTP_API_KEY", raising=False)
    monkeypatch.setenv("APTP_ENV", "development")
    monkeypatch.setenv("APTP_ALLOW_OPEN_API", "true")
    assert allow_open_api() is True
    await verify_api_key(None)


@pytest.mark.asyncio
async def test_verify_api_key_rejects_when_empty_and_open_api_disabled(monkeypatch):
    monkeypatch.delenv("APTP_API_KEY", raising=False)
    monkeypatch.setenv("APTP_ALLOW_OPEN_API", "false")
    with pytest.raises(HTTPException) as exc:
        await verify_api_key(None)
    assert exc.value.status_code == 401


def test_validate_api_key_configuration_blocks_weak_key_in_production(monkeypatch):
    monkeypatch.setenv("APTP_ENV", "production")
    monkeypatch.setenv("APTP_API_KEY", "dev-api-key")
    with pytest.raises(RuntimeError, match="strong secret"):
        validate_api_key_configuration()


def test_validate_api_key_configuration_requires_key_in_production(monkeypatch):
    monkeypatch.setenv("APTP_ENV", "production")
    monkeypatch.delenv("APTP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="APTP_API_KEY must be set"):
        validate_api_key_configuration()


def test_validate_api_key_configuration_allows_open_api_in_dev(monkeypatch):
    monkeypatch.setenv("APTP_ENV", "development")
    monkeypatch.delenv("APTP_API_KEY", raising=False)
    monkeypatch.setenv("APTP_ALLOW_OPEN_API", "true")
    validate_api_key_configuration()


def test_get_configured_api_key_strips_whitespace(monkeypatch):
    monkeypatch.setenv("APTP_API_KEY", "  my-key  ")
    assert get_configured_api_key() == "my-key"
