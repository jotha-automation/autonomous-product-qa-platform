"""Phase 2: pipeline gates execution when generator validation fails."""
from __future__ import annotations

import types

import pytest

from src.orchestrator.engine import ProductQAOrchestrator


@pytest.mark.asyncio
async def test_compile_failure_skips_execution_and_finalizes_failed(monkeypatch):
    orch = ProductQAOrchestrator()
    ctx_results: dict = {}

    async def fake_pw_generator(self, context):
        return {
            "project_path": "generated-test-projects/demo",
            "files": ["tests/smoke.spec.ts"],
            "generation_execution": {
                "skipped": False,
                "status": "validation_failed",
                "steps": [{"step": "playwright_list", "stderr": "SyntaxError: duplicate import"}],
            },
            "exploration": {},
        }

    async def fake_template_fallback(self, context, plan, project_path):
        return {
            "ok": False,
            "generation_execution": {
                "skipped": False,
                "status": "validation_failed",
                "steps": [{"step": "playwright_list", "stderr": "SyntaxError: still broken"}],
            },
        }

    execution_called = False

    async def fake_execution(self, context):
        nonlocal execution_called
        execution_called = True
        return {"status": "completed", "passed": 1, "failed": 0, "executed_tests": []}

    async def noop_emit(*_a, **_k):
        return None

    monkeypatch.setattr(
        orch.pw_generator,
        "execute",
        types.MethodType(fake_pw_generator, orch.pw_generator),
    )
    monkeypatch.setattr(
        orch.pw_generator,
        "regenerate_template_specs",
        types.MethodType(fake_template_fallback, orch.pw_generator),
    )
    monkeypatch.setattr(
        orch.execution,
        "execute",
        types.MethodType(fake_execution, orch.execution),
    )
    monkeypatch.setattr(orch, "_emit", noop_emit)
    async def noop_collect(*_a, **_k):
        return None

    monkeypatch.setattr(orch, "_collect_pipeline_evidence", noop_collect)
    monkeypatch.setattr("src.orchestrator.engine.snapshot_generated_scripts", lambda *_a, **_k: None)
    monkeypatch.setattr("src.orchestrator.engine.write_run_reports_to_disk", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.orchestrator.engine.build_all_reports",
        lambda results: {"executive": {"ok": True}},
    )

    async def fake_agent(name, agent, context, *_a, **_k):
        if name == "PWArchitect":
            return {"aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}, "scenarios": []}
        if name == "PWGenerator":
            return await fake_pw_generator(agent, context)
        if name == "Execution":
            return await fake_execution(agent, context)
        if name == "ExcelDeliverable":
            return {"deliverable_path": "/tmp/d.xlsx"}
        if name == "Executive":
            return {"score": 50}
        if name == "DefectDetection":
            return {"defects": []}
        return {}

    monkeypatch.setattr(orch, "_execute_agent", fake_agent)

    from src.orchestrator.engine import PipelineContext

    from src.agents.run_scope import bind_memory_scope, bind_pipeline_ctx, reset_memory_scope, reset_pipeline_ctx

    ctx = PipelineContext(
        run_id="run-gate-test",
        project_id="demo",
        mode="full",
        results={
            "test_plan": {"aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}, "scenarios": []},
        },
    )
    scope_token = bind_memory_scope(f"run:{ctx.run_id}")
    ctx_token = bind_pipeline_ctx(ctx)
    try:
        agent_ctx = orch._build_agent_ctx(ctx, {"metadata": {}})
        await orch._run_automation_tail(ctx, agent_ctx)
    finally:
        reset_pipeline_ctx(ctx_token)
        reset_memory_scope(scope_token)

    assert execution_called is False
    assert ctx.results["execution"]["status"] == "failed"
    assert ctx.results["execution"]["compile_errors"] == 1
    assert ctx.results["execution"]["executed_tests"] == []


def test_compile_failure_execution_result_message():
    gen = {
        "generation_execution": {
            "status": "validation_failed",
            "steps": [{"stderr": "SyntaxError: Identifier already declared"}],
        },
    }
    result = ProductQAOrchestrator._compile_failure_execution_result(gen)
    assert result["failed"] == 1
    assert result["failures"][0]["error"]["category"] == "compile"
    assert "SyntaxError" in result["stderr"]
