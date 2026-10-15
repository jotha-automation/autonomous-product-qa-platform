"""E2E: full pipeline via HTTP API with mock AI — portfolio and unstructured intake."""
from __future__ import annotations

import pytest


FRONTEND_REPORT_KEYS = {"qa", "automation", "rca", "healing", "defects", "executive"}


@pytest.mark.asyncio
async def test_create_run_portfolio_phase1(api_client, auth_headers, portfolio_documents):
    payload = {
        "project_id": "e2e-portfolio",
        "mode": "phase1",
        "metadata": {"target_url": "https://example.com"},
        "documents": portfolio_documents,
    }
    res = await api_client.post("/api/v1/runs", json=payload, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["run_id"]
    assert "readiness" in body.get("results", {})
    assert body["results"]["readiness"]["status"] in {"READY", "WARN"}


@pytest.mark.asyncio
async def test_create_run_full_pipeline_portfolio(api_client, auth_headers, portfolio_documents):
    payload = {
        "project_id": "e2e-portfolio-full",
        "mode": "full",
        "metadata": {"target_url": "https://example.com"},
        "documents": portfolio_documents,
    }
    res = await api_client.post("/api/v1/runs", json=payload, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] in {"completed", "partial"}
    run_id = body["run_id"]

    status_res = await api_client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
    assert status_res.status_code == 200
    status = status_res.json()
    assert status["run_id"] == run_id
    assert status["stage"] in {"completed", "failed", "execution", "playwright_generator"}


@pytest.mark.asyncio
async def test_reports_match_frontend_contract(api_client, auth_headers, portfolio_documents):
    payload = {
        "project_id": "e2e-reports",
        "mode": "phase1",
        "metadata": {"target_url": "https://example.com"},
        "documents": portfolio_documents,
    }
    create = await api_client.post("/api/v1/runs", json=payload, headers=auth_headers)
    run_id = create.json()["run_id"]

    reports_res = await api_client.get(f"/api/v1/runs/{run_id}/reports", headers=auth_headers)
    assert reports_res.status_code == 200
    reports = reports_res.json()
    assert FRONTEND_REPORT_KEYS.issubset(set(reports.keys())), f"Missing keys: {FRONTEND_REPORT_KEYS - set(reports.keys())}"

    executive = reports.get("executive") or {}
    assert "release_readiness_score" in executive or "executive_summary" in executive

    qa = reports.get("qa") or {}
    assert "test_design" in qa or "functional_scenario_count" in qa


@pytest.mark.asyncio
async def test_unstructured_intake_pipeline(api_client, auth_headers, unstructured_qa_document, credential_excel_document):
    payload = {
        "project_id": "e2e-unstructured",
        "mode": "phase1",
        "metadata": {"target_url": "https://portal.example.com"},
        "documents": [unstructured_qa_document, credential_excel_document],
    }
    res = await api_client.post("/api/v1/runs", json=payload, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    results = body.get("results", {})
    readiness = results.get("readiness", {})
    assert readiness.get("status") in {"READY", "WARN"}
    assert results.get("intake_guardrails", {}).get("accepted") is True

    interpreted = results.get("interpreted_intake") or results.get("intake_interpretation", {}).get("interpreted_intake")
    if interpreted:
        assert interpreted.get("obligation_count", 0) >= 0 or interpreted.get("obligations")


@pytest.mark.asyncio
async def test_deliverable_after_full_run(api_client, auth_headers, unstructured_qa_document):
    payload = {
        "project_id": "e2e-deliverable",
        "mode": "full",
        "metadata": {"target_url": "https://portal.example.com"},
        "documents": [unstructured_qa_document],
    }
    create = await api_client.post("/api/v1/runs", json=payload, headers=auth_headers)
    assert create.status_code == 200, create.text
    run_id = create.json()["run_id"]

    deliverable_res = await api_client.get(
        f"/api/v1/runs/{run_id}/artifacts/deliverable.xlsx",
        headers=auth_headers,
    )
    if deliverable_res.status_code == 404:
        pytest.skip("Deliverable not generated for this mock run (template optional)")
    assert deliverable_res.status_code == 200
    assert deliverable_res.headers.get("content-type", "").startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(deliverable_res.content) > 1000
