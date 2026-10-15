"""E2E: Drive project create → process flow (mock Drive + optional DB)."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
API_KEY = "test-e2e-api-key"


def _sync_client() -> TestClient:
    from src.main import app

    return TestClient(app)


def _multipart_payload(folder_name: str, *, trigger: str = "false") -> tuple[dict, list]:
    data: dict = {
        "folder_name": folder_name,
        "project_id": f"E2E-{uuid.uuid4().hex[:6]}",
        "target_url": "https://example.com",
        "trigger_processing": trigger,
        "mode": "phase1",
    }
    files = []
    relative_paths = []
    for name in ("brd.txt", "prd.txt", "uc.txt", "ac.txt"):
        doc_path = FIXTURES / name
        if doc_path.is_file():
            relative_paths.append(name)
            files.append(("files", (name, doc_path.read_bytes(), "text/plain")))
    data["relative_paths"] = relative_paths
    return data, files


def test_create_project_via_multipart(unique_project_name, require_postgres_for_drive_sync):
    data, files = _multipart_payload(unique_project_name)
    if not files:
        pytest.skip("Fixture documents missing")

    with _sync_client() as client:
        res = client.post(
            "/api/v1/drive/projects",
            data=data,
            files=files,
            headers={"X-Api-Key": API_KEY},
        )
        if res.status_code == 503:
            pytest.skip("Drive/DB unavailable in this environment")
        if res.status_code in {400, 500} and ("GOOGLE_DRIVE" in res.text or "Connect call failed" in res.text):
            pytest.skip("Drive/DB unavailable for multipart project create")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["drive_folder_id"]
        assert body["files_uploaded"] >= 4

        list_res = client.get("/api/v1/drive/projects", headers={"X-Api-Key": API_KEY})
    assert list_res.status_code == 200
    projects = list_res.json()["projects"]
    assert any(p["drive_folder_id"] == body["drive_folder_id"] for p in projects)


@pytest.mark.asyncio
async def test_process_project_triggers_pipeline(unique_project_name, require_postgres_for_drive):
    brd = FIXTURES / "brd.txt"
    if not brd.is_file():
        pytest.skip("Fixture documents missing")

    def _create():
        return _sync_client().post(
            "/api/v1/drive/projects",
            data={
                "folder_name": unique_project_name,
                "target_url": "https://example.com",
                "trigger_processing": "false",
                "relative_paths": ["brd.txt"],
            },
            files=[("files", ("brd.txt", brd.read_bytes(), "text/plain"))],
            headers={"X-Api-Key": API_KEY},
        )

    create = await asyncio.to_thread(_create)
    if create.status_code == 503:
        pytest.skip("Drive/DB unavailable")
    if create.status_code in {400, 500} and ("GOOGLE_DRIVE" in create.text or "Connect call failed" in create.text):
        pytest.skip("Drive/DB unavailable for project create")
    assert create.status_code == 200, create.text
    folder_id = create.json()["drive_folder_id"]

    def _process():
        return _sync_client().post(
            f"/api/v1/drive/projects/{folder_id}/process",
            json={"mode": "phase1", "force_reprocess": True},
            headers={"X-Api-Key": API_KEY},
        )

    process = await asyncio.to_thread(_process)
    if process.status_code == 503:
        pytest.skip("Drive/DB unavailable during process")
    assert process.status_code == 200, process.text
    run_id = process.json().get("pipeline_run_id")
    assert run_id

    async def _poll():
        from httpx import ASGITransport, AsyncClient
        from src.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            for _ in range(120):
                status = await client.get(
                    f"/api/v1/runs/{run_id}",
                    headers={"X-Api-Key": API_KEY},
                )
                if status.status_code != 200:
                    return status
                if status.json().get("stage") in {"completed", "failed"}:
                    return status
                await asyncio.sleep(0.5)
        return status

    final = await _poll()
    assert final.status_code == 200
    assert final.json()["stage"] in {"completed", "failed"}
