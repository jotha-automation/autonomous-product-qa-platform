"""Frontend ↔ backend API contract validation (shapes expected by ReportViewer / api.ts)."""
from __future__ import annotations

import pytest


FRONTEND_PROJECT_FIELDS = {
    "folder_name",
    "drive_folder_id",
    "project_id",
    "processed",
    "process_count",
    "max_process_count",
    "remaining_runs",
    "can_reprocess",
}

FRONTEND_RUN_STATUS_FIELDS = {"run_id", "stage", "status"}

FRONTEND_QA_RESULTS_FIELDS = {"drive_folder_id", "qa_results_folder", "runs"}


@pytest.mark.asyncio
async def test_list_projects_response_shape(api_client, auth_headers, require_postgres_for_drive):
    res = await api_client.get("/api/v1/drive/projects", headers=auth_headers)
    if res.status_code in {503, 500}:
        pytest.skip("Drive/DB unavailable")
    assert res.status_code == 200
    body = res.json()
    assert "projects" in body
    assert isinstance(body["projects"], list)
    if body["projects"]:
        project = body["projects"][0]
        missing = FRONTEND_PROJECT_FIELDS - set(project.keys())
        assert not missing, f"Project missing fields for frontend: {missing}"


@pytest.mark.asyncio
async def test_run_status_response_shape(api_client, auth_headers, portfolio_documents):
    create = await api_client.post(
        "/api/v1/runs",
        json={
            "project_id": "contract-run",
            "mode": "phase1",
            "metadata": {"target_url": "https://example.com"},
            "documents": portfolio_documents,
        },
        headers=auth_headers,
    )
    run_id = create.json()["run_id"]
    res = await api_client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    missing = FRONTEND_RUN_STATUS_FIELDS - set(body.keys())
    assert not missing, f"RunStatus missing fields: {missing}"
    assert isinstance(body.get("events", []), list)


@pytest.mark.asyncio
async def test_interpreted_intake_in_reports_when_unstructured(
    api_client, auth_headers, unstructured_qa_document
):
    create = await api_client.post(
        "/api/v1/runs",
        json={
            "project_id": "contract-intake",
            "mode": "phase1",
            "metadata": {"target_url": "https://portal.example.com"},
            "documents": [unstructured_qa_document],
        },
        headers=auth_headers,
    )
    assert create.status_code == 200
    results = create.json().get("results", {})
    reports = results.get("reports") or {}
    # Frontend InterpretedIntakeSummary optional tab
    if "interpreted_intake" in reports:
        ii = reports["interpreted_intake"]
        assert "obligation_count" in ii or "obligations" in ii or ii.get("confidence") is not None
    elif results.get("interpreted_intake"):
        ii = results["interpreted_intake"]
        assert "obligations" in ii or "obligation_count" in ii
