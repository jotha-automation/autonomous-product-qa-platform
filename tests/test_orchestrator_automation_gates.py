"""Orchestrator gates: validation_failed fallback and partial finalize on exec failure."""
import pytest

from src.orchestrator.engine import ProductQAOrchestrator, PipelineContext
from src.orchestrator.stages import PipelineStage


@pytest.mark.asyncio
async def test_maybe_template_fallback_after_validation_failure(tmp_path, monkeypatch):
    orch = ProductQAOrchestrator()
    ctx = PipelineContext(run_id="run-gate", project_id="demo")
    plan = {"aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}, "scenarios": []}
    gen = {
        "project_path": str(tmp_path),
        "generation_execution": {"status": "validation_failed", "steps": []},
        "exploration": {},
    }

    async def fake_regen(_ctx, _plan, _path):
        return {
            "ok": True,
            "files": ["tests/manual.spec.ts"],
            "generation_execution": {"status": "completed", "steps": []},
        }

    monkeypatch.setattr(orch.pw_generator, "regenerate_template_specs", fake_regen)
    merged = await orch._maybe_template_fallback_after_validation_failure(ctx, gen, plan, {})
    assert merged["template_only"] is True
    assert merged["generation_execution"]["status"] == "completed"


@pytest.mark.asyncio
async def test_generator_validation_gates_execution(monkeypatch):
    orch = ProductQAOrchestrator()
    ctx = PipelineContext(run_id="run-skip-exec", project_id="demo")
    gen = {
        "project_path": "/tmp/demo",
        "generation_execution": {
            "status": "validation_failed",
            "steps": [{"stderr": "SyntaxError: duplicate import"}],
        },
    }
    exec_result = orch._compile_failure_execution_result(gen)
    assert exec_result["status"] == "failed"
    assert exec_result["executed_tests"] == []
    assert exec_result["compile_errors"] == 1
    assert exec_result["failures"][0]["error"]["category"] == "compile"


@pytest.mark.asyncio
async def test_finalize_partial_on_execution_failure_orchestrator():
    orch = ProductQAOrchestrator()
    ctx = PipelineContext(run_id="run-partial", project_id="demo")
    ctx.stage = PipelineStage.EXECUTION
    ctx.results["execution"] = {
        "status": "failed",
        "passed": 0,
        "failed": 3,
        "total": 0,
        "executed_tests": [],
        "compile_errors": 3,
    }
    result = await orch._finalize(ctx, partial=True)
    assert result["status"] == "partial"
    assert result["partial"] is True

    ctx2 = PipelineContext(run_id="run-failed", project_id="demo")
    ctx2.stage = PipelineStage.EXECUTION
    ctx2.results["execution"] = {
        "status": "failed",
        "passed": 0,
        "failed": 3,
        "total": 0,
        "executed_tests": [],
        "compile_errors": 3,
    }
    result2 = await orch._finalize(ctx2, failed=True)
    assert result2["status"] == "failed"


def test_is_compile_failure_detects_duplicate_import():
    orch = ProductQAOrchestrator()
    exec_result = {
        "compile_errors": 3,
        "failures": [
            {
                "title": "manual.spec.ts",
                "error": {
                    "message": "SyntaxError: Identifier 'captureStepEvidence' has already been declared",
                    "category": "compile",
                },
            }
        ],
    }
    rca = {"failure_category": "compile"}
    assert orch._is_compile_failure(exec_result, rca) is True
