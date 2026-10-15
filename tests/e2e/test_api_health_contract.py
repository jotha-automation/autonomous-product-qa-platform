"""E2E: health endpoint matches frontend Settings expectations."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_service_status(api_client, auth_headers):
    res = await api_client.get("/health", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["service"] == "aptp"
    assert "status" in body
    assert "llm" in body
    assert "drive" in body
    assert body["llm"]["mode"] == "mock" or body["llm"].get("status") == "ok"
    assert "data_provenance" in body


@pytest.mark.asyncio
async def test_health_requires_no_api_key(api_client):
    res = await api_client.get("/health")
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_runs_requires_api_key(api_client):
    res = await api_client.get("/api/v1/runs/run-does-not-exist")
    assert res.status_code == 401
