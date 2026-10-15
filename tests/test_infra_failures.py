"""Tests for infrastructure failure detection and reporting consolidation."""
import json
from pathlib import Path

import pytest

from src.agents.defect_detection import DefectDetectionAgent
from src.reporting.error_sanitization import (
    consolidate_execution_failures,
    sanitize_playwright_error,
    validation_blocked_context,
)
from src.reporting.infra_failures import (
    apply_infrastructure_execution_semantics,
    consolidate_infrastructure_defects,
    detect_execution_infrastructure_blocker,
    is_infrastructure_failure_message,
)
from src.reporting.visual_report import build_visual_report

_BROWSER_ERROR = (
    "Error: browserType.launch: Executable doesn't exist at "
    "/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell\n"
    "╔══════════════════════════════════════════════════════╗\n"
    "║ Looks like Playwright was just updated to 1.61.1.    ║\n"
    "║ Please update docker image as well.                  ║\n"
    "║ -  current: mcr.microsoft.com/playwright:v1.40.0-jammy ║\n"
    "║ - required: mcr.microsoft.com/playwright:v1.61.1-jammy ║\n"
    "╚══════════════════════════════════════════════════════╝"
)


def _browser_mismatch_execution(failed: int = 3) -> dict:
    failures = [
        {
            "title": f"OBL-EMP-{i:05d}: test case {i}",
            "error": {"message": _BROWSER_ERROR},
        }
        for i in range(1, failed + 1)
    ]
    executed_tests = [
        {
            "id": f"OBL-EMP-{i:05d}",
            "title": failures[i - 1]["title"],
            "status": "failed",
            "error": _BROWSER_ERROR,
        }
        for i in range(1, failed + 1)
    ]
    return {
        "status": "failed",
        "mode": "live",
        "passed": 0,
        "failed": failed,
        "total": failed,
        "pass_rate": 0.0,
        "failures": failures,
        "executed_tests": executed_tests,
    }


def test_is_infrastructure_failure_message_detects_docker_mismatch():
    assert is_infrastructure_failure_message(_BROWSER_ERROR)


def test_sanitize_playwright_error_strips_box_drawing():
    clean = sanitize_playwright_error(_BROWSER_ERROR, max_len=1000)
    assert "╔" not in clean
    assert "browserType.launch" in clean
    assert "mcr.microsoft.com/playwright:v1.61.1-jammy" in clean


def test_detect_execution_infrastructure_blocker_homogeneous_failures():
    execution = _browser_mismatch_execution(32)
    blocker = detect_execution_infrastructure_blocker(execution)
    assert blocker is not None
    assert blocker["failure_kind"] == "browser_launch"
    assert blocker["affected_test_count"] == 32
    assert blocker["required_docker_image"] == "mcr.microsoft.com/playwright:v1.61.1-jammy"
    assert blocker["current_docker_image"] == "mcr.microsoft.com/playwright:v1.40.0-jammy"
    assert len(blocker["remediation_steps"]) >= 2


def test_consolidate_execution_failures_returns_single_blocker():
    execution = _browser_mismatch_execution(5)
    per_test, blocker = consolidate_execution_failures(execution["failures"], execution=execution)
    assert per_test == []
    assert blocker is not None
    assert blocker["category"] == "infrastructure"
    assert blocker["affected_tests_count"] == 5


def test_apply_infrastructure_execution_semantics_reclassifies_tests():
    execution = _browser_mismatch_execution(2)
    blocker = detect_execution_infrastructure_blocker(execution)
    updated = apply_infrastructure_execution_semantics(execution, blocker)
    assert updated["tests_failed"] == 0
    assert updated["tests_blocked"] == 2
    assert updated["failed"] == 0
    assert all(t["status"] == "blocked" for t in updated["executed_tests"])


def test_consolidate_infrastructure_defects_collapses_duplicates():
    execution = _browser_mismatch_execution(3)
    blocker = detect_execution_infrastructure_blocker(execution)
    raw_defects = [
        {
            "id": f"DEF-{i:03d}",
            "severity": "high",
            "category": "test_failure",
            "title": f"case {i}",
            "description": _BROWSER_ERROR,
            "source": "playwright_execution",
        }
        for i in range(1, 4)
    ]
    consolidated = consolidate_infrastructure_defects(raw_defects, blocker, execution=execution)
    assert len(consolidated) == 1
    assert consolidated[0]["category"] == "infrastructure"
    assert consolidated[0]["id"] == "DEF-INFRA-001"


def test_validation_blocked_context_true_for_browser_mismatch():
    ctx = validation_blocked_context(_browser_mismatch_execution(4))
    assert ctx["validation_blocked"] is True
    assert ctx["blocker_reason"] == "infrastructure"
    assert ctx["infrastructure_blocker"] is not None


def test_build_visual_report_infra_run_0113cfc5e3bd():
    artifact = Path(
        "generated-test-projects/DEMO-QA/aptp-artifacts/run-0113cfc5e3bd/pipeline_summary.json"
    )
    if not artifact.is_file():
        pytest.skip("run-0113cfc5e3bd artifacts not available locally")

    summary = json.loads(artifact.read_text(encoding="utf-8"))
    visual = build_visual_report("run-0113cfc5e3bd", summary)
    exec_section = visual["executive"]

    assert exec_section["tests_failed"] == 0
    assert exec_section["tests_blocked"] == 32
    assert exec_section["validation_blocked"] is True
    assert exec_section["infrastructure_blocker"] is not None
    assert exec_section["defects_total"] <= 15
    assert len(exec_section["critical_defects"]) <= 10
    assert exec_section["critical_defects"][0]["category"] == "infrastructure"
    assert "╔" not in exec_section["critical_defects"][0].get("description", "")

    matrix = exec_section["test_matrix_summary"]
    assert matrix["scenarios_designed"] >= 32
    assert matrix["scenarios_executed"] == 32
    assert matrix["scenarios_in_matrix"] >= matrix["scenarios_designed"]
    assert "diseñados" in matrix["visibility_note"].lower()

    blocked_cases = [c for c in visual["test_cases"] if c["status"] == "blocked"]
    assert len(blocked_cases) == 32


@pytest.mark.asyncio
async def test_defect_detection_consolidates_infra_failures(monkeypatch):
    agent = DefectDetectionAgent.__new__(DefectDetectionAgent)
    agent.memory = None

    monkeypatch.setattr("src.agents.defect_detection.output_tokens", lambda *_a, **_k: 128)

    async def _fake_ask_json(*_args, **_kwargs):
        return {
            "recommendations": ["Fix Docker image"],
            "release_blocker": True,
            "release_readiness": "validation_blocked",
            "priority_order": ["DEF-001"],
            "defects_enriched": [],
        }

    monkeypatch.setattr(agent, "ask_json", _fake_ask_json)
    monkeypatch.setattr(agent, "compact_json", lambda x, **_: json.dumps(x)[:200])
    monkeypatch.setattr(agent, "store_short_term_memory", lambda *_a, **_k: None)

    result = await agent.execute(
        {
            "execution_results": _browser_mismatch_execution(5),
            "coverage": {},
            "readiness": {},
            "rca": {"failure_category": "infra", "root_cause": _BROWSER_ERROR},
            "healing": {},
            "test_plan": {"scenarios": [{"id": "s1"}]},
        }
    )

    assert result["validation_blocked"] is True
    assert result["suppressed_duplicate_failures"] == 5
    assert result["total"] <= 2
    infra = [d for d in result["defects"] if d.get("category") == "infrastructure"]
    assert len(infra) == 1
    assert "╔" not in infra[0]["description"]
