"""Tests for blocking target URL gate before intake interpretation."""
from __future__ import annotations

import uuid

import pytest

from src.orchestrator.engine import ProductQAOrchestrator
from src.services.pipeline_checkpoint import clear_checkpoint, is_awaiting_target_url, load_checkpoint
from src.services.run_hints import set_run_hints
from src.services.target_url import intake_has_target_url


@pytest.fixture
def docs_without_url():
    return [
        {
            "id": "req",
            "filename": "requirements.pdf",
            "document_type": "PRD",
            "raw_text": "Login flow and registration module for QA validation.",
            "sections": [],
        }
    ]


@pytest.mark.asyncio
async def test_pipeline_pauses_when_target_url_missing(docs_without_url):
    orch = ProductQAOrchestrator()
    run_id = f"run-gate-test-{uuid.uuid4().hex[:8]}"
    payload = {
        "run_id": run_id,
        "project_id": "gate-test",
        "mode": "phase1",
        "documents": docs_without_url,
        "metadata": {},
    }
    result = await orch.run_phase1(payload)
    assert result["status"] == "awaiting_input"
    assert result["stage"] == "awaiting_target_url"
    assert is_awaiting_target_url(run_id)
    cp = load_checkpoint(run_id)
    assert cp and cp.get("awaiting") == "target_url"
    assert "intake_index" in result.get("results", {})


@pytest.mark.asyncio
async def test_pipeline_resumes_after_target_url_provided(docs_without_url):
    from src.memory.enhanced_memory import get_memory

    run_id = f"run-gate-test-{uuid.uuid4().hex[:8]}"
    get_memory().delete(f"run_hints:{run_id}")
    clear_checkpoint(run_id)
    orch = ProductQAOrchestrator()
    paused = await orch.run_phase1(
        {
            "run_id": run_id,
            "project_id": "gate-test",
            "mode": "phase1",
            "documents": docs_without_url,
            "metadata": {},
        }
    )
    assert paused["status"] == "awaiting_input"

    set_run_hints(run_id, {"target_url": "https://staging.example.com", "operator_note": "Operator URL"})
    resumed = await orch.run_phase1(
        {
            "run_id": run_id,
            "project_id": "gate-test",
            "mode": "phase1",
            "documents": docs_without_url,
            "metadata": {"target_url": "https://staging.example.com"},
            "resume_from": "intake_interpretation",
        }
    )
    assert resumed["status"] in {"completed", "partial"}
    assert not is_awaiting_target_url(run_id)


def test_intake_has_target_url_from_metadata(docs_without_url):
    assert not intake_has_target_url(docs_without_url, {}, "")
    assert intake_has_target_url(
        docs_without_url,
        {"target_url": "https://app.example.com"},
        "",
    )
