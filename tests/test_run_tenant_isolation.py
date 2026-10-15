"""Tenant/project isolation on run resource downloads (Phase 5c)."""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.deps import enforce_run_project_scope, resolve_run_project_id_sync


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-Api-Key": os.environ.get("APTP_API_KEY", "test-api-key")}


@pytest.mark.asyncio
async def test_enforce_run_project_scope_allows_matching_project(monkeypatch):
    run_id = "run-tenant-ok"

    async def _resolve(rid: str):
        return "proj-a" if rid == run_id else None

    monkeypatch.setattr("src.api.deps.resolve_run_project_id", _resolve)
    await enforce_run_project_scope(run_id, x_project_id="proj-a")


@pytest.mark.asyncio
async def test_enforce_run_project_scope_rejects_mismatch(monkeypatch):
    run_id = "run-tenant-bad"

    async def _resolve(rid: str):
        return "proj-a" if rid == run_id else None

    monkeypatch.setattr("src.api.deps.resolve_run_project_id", _resolve)
    with pytest.raises(HTTPException) as exc:
        await enforce_run_project_scope(run_id, project_id="proj-b")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_enforce_run_project_scope_skips_when_no_project_claimed(monkeypatch):
    run_id = "run-no-scope"

    async def _resolve(_rid: str):
        raise AssertionError("should not resolve")

    monkeypatch.setattr("src.api.deps.resolve_run_project_id", _resolve)
    await enforce_run_project_scope(run_id)


def test_resolve_run_project_id_sync_from_memory(monkeypatch):
    run_id = "run-mem"

    class _Mem:
        def read(self, key):
            if key == f"pipeline_summary:{run_id}":
                return {"project_id": "proj-mem"}
            return None

    monkeypatch.setattr("src.memory.enhanced_memory.get_memory", lambda: _Mem())
    assert resolve_run_project_id_sync(run_id) == "proj-mem"


@pytest.mark.asyncio
async def test_get_run_access_token_endpoint_issues_token(api_client, auth_headers, monkeypatch):
    run_id = "run-token-endpoint"
    monkeypatch.setattr(
        "src.api.routes.runs.ProductQAOrchestrator.get_pipeline_status",
        lambda self, rid: {"run_id": rid, "status": "running"} if rid == run_id else None,
    )
    async def _resolve_project(rid: str):
        return "proj-x" if rid == run_id else None

    monkeypatch.setattr("src.api.routes.runs.resolve_run_project_id", _resolve_project)
    res = await api_client.get(f"/api/v1/runs/{run_id}/access-token", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["run_id"] == run_id
    assert body["token"]
    assert body["expires_at"] > 0
