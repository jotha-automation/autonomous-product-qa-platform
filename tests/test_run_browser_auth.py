"""Browser-facing run routes accept ephemeral tokens (Phase 5b)."""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.run_access_token import issue_run_access_token


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("APTP_API_KEY", os.environ.get("APTP_API_KEY", "test-api-key"))


@pytest.mark.asyncio
async def test_stream_rejects_without_credentials(api_client, monkeypatch):
    run_id = "run-stream-auth"
    monkeypatch.setattr(
        "src.api.routes.runs.ProductQAOrchestrator.get_pipeline_status",
        lambda self, rid: {"run_id": rid, "status": "running"} if rid == run_id else None,
    )
    res = await api_client.get(f"/api/v1/runs/{run_id}/stream")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_stream_accepts_run_access_token(api_client, monkeypatch):
    run_id = "run-stream-token"
    token = issue_run_access_token(run_id)["token"]
    monkeypatch.setattr(
        "src.api.routes.runs.ProductQAOrchestrator.get_pipeline_status",
        lambda self, rid: {"run_id": rid, "status": "failed", "stage": "failed"} if rid == run_id else None,
    )
    monkeypatch.setattr("src.api.routes.runs.get_run_events", lambda rid: [{"type": "run_failed"}] if rid == run_id else [])
    monkeypatch.setattr("src.api.routes.runs.run_terminal_event_emitted", lambda events: True)
    res = await api_client.get(f"/api/v1/runs/{run_id}/stream?token={token}")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_stream_rejects_api_key_query_param(api_client, monkeypatch):
    run_id = "run-stream-no-query-key"
    monkeypatch.setattr(
        "src.api.routes.runs.ProductQAOrchestrator.get_pipeline_status",
        lambda self, rid: {"run_id": rid, "status": "running"} if rid == run_id else None,
    )
    res = await api_client.get(
        f"/api/v1/runs/{run_id}/stream",
        params={"api_key": os.environ["APTP_API_KEY"]},
    )
    assert res.status_code == 401
