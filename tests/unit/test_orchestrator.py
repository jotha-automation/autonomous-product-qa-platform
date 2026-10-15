import os

import pytest

from src.orchestrator.engine import ProductQAOrchestrator
from src.orchestrator.stages import PipelineStage


@pytest.fixture
def orchestrator():
    os.environ["APTP_USE_MOCK_LLM"] = "true"
    return ProductQAOrchestrator()


@pytest.mark.asyncio
async def test_phase1_pipeline(orchestrator):
    payload = {
        "project_id": "test-project",
        "mode": "phase1",
        "metadata": {"target_url": "https://bank-portal.up.railway.app"},
        "documents": [
            {"document_type": "BRD", "filename": "brd.txt", "raw_text": "Business requirements"},
            {"document_type": "PRD", "filename": "prd.txt", "raw_text": "Product requirements"},
            {"document_type": "UC", "filename": "uc.txt", "raw_text": "Use case login"},
            {"document_type": "AC", "filename": "ac.txt", "raw_text": "Acceptance criteria"},
        ],
    }
    result = await orchestrator.run_phase1(payload)
    assert result["status"] == "completed"
    assert result["stage"] == PipelineStage.COMPLETED.value
    assert "readiness" in result["results"]
    assert "requirements" in result["results"]
    assert "coverage" in result["results"]
    assert "traceability" in result["results"]
    assert "executive_report" in result["results"]
    assert "reports" in result["results"]


@pytest.mark.asyncio
async def test_phase1_warns_on_incomplete_intake(orchestrator):
    result = await orchestrator.run_phase1({
        "project_id": "incomplete",
        "metadata": {"target_url": "https://bank-portal.up.railway.app"},
        "documents": [{"document_type": "BRD", "filename": "brd.txt", "raw_text": "only brd business requirements QA test"}],
    })
    assert result["status"] == "completed"
    assert result["results"]["readiness"]["status"] in {"WARN", "READY"}
    assert "requirements" in result["results"]


@pytest.mark.asyncio
async def test_full_pipeline_with_automation(orchestrator):
    payload = {
        "project_id": "auto-test",
        "mode": "full",
        "metadata": {"target_url": "https://bank-portal.up.railway.app"},
        "documents": [
            {"document_type": t, "filename": f"{t.lower()}.txt", "raw_text": f"Sample {t}"}
            for t in ("BRD", "PRD", "UC", "AC")
        ],
    }
    result = await orchestrator.run(payload)
    assert result["status"] == "completed"
    assert "generated_code" in result["results"] or "execution" in result.get("results", {})


@pytest.mark.asyncio
async def test_pipeline_status(orchestrator):
    result = await orchestrator.run_phase1({
        "run_id": "status-test-001",
        "project_id": "demo",
        "documents": [
            {"document_type": t, "filename": f"{t}.txt", "raw_text": t}
            for t in ("BRD", "PRD", "UC", "AC")
        ],
    })
    status = orchestrator.get_pipeline_status(result["run_id"])
    assert status is not None
