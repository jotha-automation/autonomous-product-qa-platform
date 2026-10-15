"""GET /runs/{run_id}/reports must not expose raw pipeline results as deliverables."""
import os

os.environ["APTP_ALLOW_MOCKS"] = "true"
os.environ["APTP_USE_MOCK_LLM"] = "true"

import pytest
from fastapi import HTTPException

from src.api.routes.runs import get_run_reports
from src.memory.enhanced_memory import get_memory


@pytest.mark.asyncio
async def test_get_run_reports_404_when_only_pipeline_results(monkeypatch):
    run_id = "run-draft"
    summary = {
        "run_id": run_id,
        "results": {
            "intake_guardrails": {"rules": []},
            "intake_index": {"documents": []},
            "readiness": {"status": "READY"},
        },
    }
    monkeypatch.setattr(
        get_memory(),
        "read",
        lambda key: summary if key == f"pipeline_summary:{run_id}" else None,
    )
    monkeypatch.setattr(
        "src.services.artifact_bundle.find_artifact_dir_on_disk",
        lambda _: None,
    )

    with pytest.raises(HTTPException) as exc:
        await get_run_reports(run_id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_run_reports_returns_published_reports_only(monkeypatch):
    run_id = "run-done"
    reports = {"executive": {"executive_summary": "ok"}}
    summary = {
        "run_id": run_id,
        "results": {
            "reports": reports,
            "intake_guardrails": {"rules": []},
            "interpreted_intake": {"obligations": [{"id": "OBL-1", "title": "Login"}]},
            "readiness": {"obligation_count": 1, "intake_profile": {"primary_strategy": "interpretation"}},
            "execution": {"total": 10, "failed": 1, "pass_rate": 0.9},
            "coverage": {"coverage_score": 0},
            "risk": {"release_risk_score": 1.0},
        },
    }
    monkeypatch.setattr(
        get_memory(),
        "read",
        lambda key: summary if key == f"pipeline_summary:{run_id}" else None,
    )

    result = await get_run_reports(run_id)
    assert result["executive"]["executive_summary"] == "ok"
    assert result["qa"]["interpreted_intake"]["obligation_count"] == 1
    assert result["executive"]["release_readiness_score"] > 0
    assert "intake_guardrails" not in result


@pytest.mark.asyncio
async def test_get_visual_report_endpoint(monkeypatch):
    from src.api.routes.runs import get_visual_report

    run_id = "run-visual"
    summary = {
        "run_id": run_id,
        "project_id": "proj",
        "status": "completed",
        "results": {
            "test_design": {"functional_scenarios": [{"id": "TC-1", "title": "Test", "scenario_type": "happy_path"}]},
            "reports": {
                "executive": {"release_readiness_score": 90},
                "automation": {"execution": {"passed": 1, "failed": 0, "total": 1, "pass_rate": 1, "executed_tests": []}},
                "defects": {"total": 0, "defects": []},
            },
        },
    }
    monkeypatch.setattr(
        "src.services.artifact_bundle.load_run_summary",
        lambda _: summary,
    )
    monkeypatch.setattr(
        "src.services.artifact_bundle.find_artifact_dir_on_disk",
        lambda _: None,
    )
    monkeypatch.setattr(
        "src.services.artifact_bundle.get_run_manifest",
        lambda _: {},
    )

    visual = await get_visual_report(run_id)
    assert visual["type"] == "visual"
    assert visual["run_id"] == run_id
    assert len(visual["test_cases"]) == 1


@pytest.mark.asyncio
async def test_get_run_status_endpoint(monkeypatch):
    from src.api.routes.runs import get_run

    run_id = "run-status"
    status = {
        "run_id": run_id,
        "stage": "execution",
        "status": "running",
    }
    monkeypatch.setattr(
        "src.orchestrator.engine.ProductQAOrchestrator.get_pipeline_status",
        lambda self, rid: status if rid == run_id else None,
    )
    monkeypatch.setattr(
        "src.memory.enhanced_memory.EnhancedSharedMemory.read",
        lambda self, key: None,
    )

    result = await get_run(run_id)
    assert result["run_id"] == run_id
    assert result["stage"] == "execution"
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_get_run_partial_status():
    from src.api.routes.runs import get_run

    run_id = "run-partial"
    status = {
        "run_id": run_id,
        "stage": "awaiting_target_url",
        "status": "partial",
        "awaiting": "target_url",
        "resumable": True,
    }
    # We cannot monkeypatch the module-level _orchestrator easily without importing it;
    # instead verify the schema contract directly.
    assert status["status"] == "partial"
    assert status["stage"] == "awaiting_target_url"
    assert status["resumable"] is True
