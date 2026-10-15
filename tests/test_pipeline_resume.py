"""Tests for pipeline failure checkpoint and resume."""
from __future__ import annotations

import types

import pytest

from src.orchestrator.engine import ProductQAOrchestrator
from src.orchestrator.stages import should_run_resume_stage
from src.services.pipeline_checkpoint import is_resumable, load_resume_checkpoint, save_failure_checkpoint


def test_should_run_resume_stage_skips_completed_steps():
    assert should_run_resume_stage("excel_deliverable", "playwright_architect") is False
    assert should_run_resume_stage("excel_deliverable", "excel_deliverable") is True
    assert should_run_resume_stage("excel_deliverable", "executive_reporting") is True
    assert should_run_resume_stage(None, "playwright_architect") is True


def test_failure_checkpoint_marks_run_resumable():
    run_id = "run-resume-test-001"
    save_failure_checkpoint(
        run_id,
        failed_stage="excel_deliverable",
        project_id="demo",
        partial_results={"execution": {"passed": 1, "failed": 0}},
        metadata={"target_url": "https://example.com"},
        start_time="2026-06-24T14:00:00",
        mode="full",
    )
    assert is_resumable(run_id)
    cp = load_resume_checkpoint(run_id)
    assert cp and cp["failed_stage"] == "excel_deliverable"
    assert cp["partial_results"]["execution"]["passed"] == 1


def test_failed_run_resumable_from_disk_artifacts(tmp_path, monkeypatch):
    from src.memory.enhanced_memory import get_memory
    from src.services import artifact_bundle
    from src.services.pipeline_checkpoint import clear_checkpoint

    run_id = "run-disk-resume-001"
    project_id = "PIPELINE-RUN-FINAL"
    clear_checkpoint(run_id)
    monkeypatch.setattr(artifact_bundle, "GENERATED_PROJECTS_ROOT", tmp_path)

    artifact_dir = tmp_path / project_id / "aptp-artifacts" / run_id
    artifact_dir.mkdir(parents=True)
    summary = {
        "run_id": run_id,
        "project_id": project_id,
        "status": "failed",
        "stage": "failed",
        "failed_at_stage": "playwright_generator",
        "start_time": "2026-07-07T04:22:00.798480",
        "metadata": {"target_url": "https://bank-portal.up.railway.app"},
        "results": {"playwright_architect": {"ok": True}},
    }
    (artifact_dir / "pipeline_summary.json").write_text(
        __import__("json").dumps(summary),
        encoding="utf-8",
    )

    memory = get_memory()
    memory.delete(f"pipeline_summary:{run_id}")
    memory.delete(f"pipeline_checkpoint:{run_id}")

    assert is_resumable(run_id)
    cp = load_resume_checkpoint(run_id)
    assert cp is not None
    assert cp["failed_stage"] == "playwright_generator"
    assert cp["project_id"] == project_id
    assert cp["partial_results"]["playwright_architect"]["ok"] is True


@pytest.mark.asyncio
async def test_resume_run_from_excel_deliverable(monkeypatch):
    run_id = "run-resume-test-002"
    partial = {
        "requirements": {"requirements": []},
        "test_design": {"functional_scenarios": []},
        "coverage": {},
        "traceability": {"traceability_matrix": []},
        "risk": {},
        "readiness": {},
        "test_plan": {"suite": {}, "scenarios": []},
        "generated_code": {"project_path": "generated-test-projects/demo", "files": []},
        "execution": {"passed": 2, "failed": 0, "executed_tests": []},
        "heal_attempts": 0,
        "defects": {"defects": []},
        "interpreted_intake": {"obligations": []},
    }
    save_failure_checkpoint(
        run_id,
        failed_stage="excel_deliverable",
        project_id="demo",
        partial_results=partial,
        metadata={"target_url": "https://example.com"},
        start_time="2026-06-24T14:00:00",
        mode="full",
    )

    orch = ProductQAOrchestrator()
    calls: list[str] = []

    async def fake_excel(self, context):
        calls.append("excel")
        return {"deliverable_path": "/tmp/deliverable.xlsx"}

    async def fake_executive(self, context):
        calls.append("executive")
        return {"score": 80}

    async def noop_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        orch.excel_deliverable,
        "execute",
        types.MethodType(fake_excel, orch.excel_deliverable),
    )
    monkeypatch.setattr(
        orch.executive,
        "execute",
        types.MethodType(fake_executive, orch.executive),
    )
    monkeypatch.setattr(orch, "_emit", noop_emit)
    monkeypatch.setattr(
        "src.orchestrator.engine.build_all_reports",
        lambda results: {"executive": {"ok": True}},
    )
    monkeypatch.setattr("src.orchestrator.engine.write_run_reports_to_disk", lambda *_a, **_k: None)

    async def noop_collect(*_a, **_k):
        return None

    monkeypatch.setattr(orch, "_collect_pipeline_evidence", noop_collect)
    monkeypatch.setattr("src.orchestrator.engine.snapshot_generated_scripts", lambda *_a, **_k: None)

    result = await orch.resume_run(run_id)

    assert result["status"] == "completed"
    assert calls == ["excel", "executive"]
    assert "deliverable" in result["results"]
