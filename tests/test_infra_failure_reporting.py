"""Tests for Playwright error sanitization and infrastructure failure consolidation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.defect_detection import DefectDetectionAgent
from src.agents.executive_reporting import (
    ExecutiveReportingAgent,
    compute_design_readiness_score,
)
from src.agents.failure_rca import FailureRCAAgent
from src.reporting.error_sanitization import (
    consolidate_execution_failures,
    is_infrastructure_failure,
    sanitize_playwright_error,
    validation_blocked_context,
)
from src.reporting.infra_failures import detect_execution_infrastructure_blocker

PLAYWRIGHT_BOX_ERROR = """Error: browserType.launch: Executable doesn't exist at /ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell
╔════════════════════════════════════════════════════════╗
║ Looks like Playwright was just updated to 1.61.1.      ║
║ Please update docker image as well.                    ║
║ -  current: mcr.microsoft.com/playwright:v1.40.0-jammy ║
║ - required: mcr.microsoft.com/playwright:v1.61.1-jammy ║
║                                                        ║
║ <3 Playwright Team                                     ║
╚════════════════════════════════════════════════════════╝"""


def _make_failures(count: int = 3) -> list[dict]:
    return [
        {
            "title": f"OBL-EMP-{1000 + i}: test-{i}",
            "error": {"message": PLAYWRIGHT_BOX_ERROR},
        }
        for i in range(count)
    ]


def test_sanitize_playwright_error_strips_box_art():
    clean = sanitize_playwright_error(PLAYWRIGHT_BOX_ERROR)
    assert "╔" not in clean
    assert "║" not in clean
    assert "browserType.launch" in clean
    assert "mcr.microsoft.com/playwright:v1.61.1-jammy" in clean


def test_is_infrastructure_failure_detects_browser_executable():
    assert is_infrastructure_failure(PLAYWRIGHT_BOX_ERROR)
    assert is_infrastructure_failure("", rca_category="infra")
    assert not is_infrastructure_failure("Timeout 15000ms waiting for locator")


def test_consolidate_execution_failures_single_blocker():
    failures = _make_failures(32)
    per_test, blocker = consolidate_execution_failures(
        failures,
        rca={"failure_category": "infra"},
        execution={"failed": 32, "stderr": ""},
    )
    assert per_test == []
    assert blocker is not None
    assert blocker["category"] == "infrastructure"
    assert blocker["affected_tests_count"] == 32
    assert "remediation" in blocker or blocker.get("remediation_steps")


def test_detect_execution_infrastructure_blocker_from_run_shape():
    execution = {
        "failed": 32,
        "failures": _make_failures(32),
        "executed_tests": [{"title": "t1", "status": "failed", "error": PLAYWRIGHT_BOX_ERROR}],
    }
    blocker = detect_execution_infrastructure_blocker(execution)
    assert blocker is not None
    assert blocker["failure_kind"] == "browser_launch"
    assert blocker["required_docker_image"] == "mcr.microsoft.com/playwright:v1.61.1-jammy"


@pytest.mark.asyncio
async def test_defect_detection_consolidates_infra_failures():
    agent = DefectDetectionAgent.__new__(DefectDetectionAgent)
    agent.memory = None
    agent.store_short_term_memory = lambda *_a, **_k: None  # type: ignore[method-assign]
    agent.compact_json = lambda data, **_: json.dumps(data)  # type: ignore[method-assign]

    async def fake_ask_json(task, fallback, **_k):
        return fallback

    agent.ask_json = fake_ask_json  # type: ignore[method-assign]

    context = {
        "execution_results": {
            "failed": 32,
            "pass_rate": 0.0,
            "failures": _make_failures(32),
        },
        "rca": {"failure_category": "infra", "root_cause": "Docker mismatch"},
        "coverage": {},
        "readiness": {},
        "test_plan": {"scenarios": [{}] * 136},
    }

    result = await agent.execute(context)
    infra_defects = [d for d in result["defects"] if d.get("category") == "infrastructure"]
    test_failures = [d for d in result["defects"] if d.get("category") == "test_failure"]
    assert len(infra_defects) == 1
    assert test_failures == []
    assert result["suppressed_duplicate_failures"] == 32
    assert result["validation_blocked"] is True
    assert result["release_readiness"] == "validation_blocked"
    assert "╔" not in infra_defects[0]["description"]


@pytest.mark.asyncio
async def test_executive_reporting_blocked_environment_fallback():
    agent = ExecutiveReportingAgent.__new__(ExecutiveReportingAgent)
    agent.memory = None
    agent.read_memory = lambda *_a, **_k: {}  # type: ignore[method-assign]
    agent.store_short_term_memory = lambda *_a, **_k: None  # type: ignore[method-assign]
    agent.compact_json = lambda data, **_: json.dumps(data)  # type: ignore[method-assign]

    async def fake_ask_json(task, fallback, **_k):
        return fallback

    agent.ask_json = fake_ask_json  # type: ignore[method-assign]

    context = {
        "readiness": {"score": 0.95, "status": "READY"},
        "coverage": {"coverage_score": 0.0},
        "risk": {"release_risk_score": 1.0},
        "traceability": {"traceability_matrix": [{"coverage": "full"}] * 10},
        "execution_results": {
            "failed": 32,
            "total": 32,
            "failures": _make_failures(32),
        },
        "rca": {"failure_category": "infra"},
        "test_plan": {"scenarios": [{}] * 136},
        "defects": {"total": 1, "suppressed_duplicate_failures": 32},
    }

    result = await agent.execute(context)
    assert result["validation_blocked"] is True
    assert result["product_readiness_verdict"] == "blocked_by_environment"
    assert result["environment_status"] == "not_ready"
    assert result["release_readiness_score"] > 0
    assert "infraestructura" in result["executive_summary"].casefold() or "entorno" in result["executive_summary"].casefold()


def test_failure_rca_fallback_infra_category():
    agent = FailureRCAAgent.__new__(FailureRCAAgent)
    failures = _make_failures(2)
    result = agent._fallback(failures, {"failed": 2})
    assert result["failure_category"] == "infra"
    assert result["action_items"][0]["owner"] == "ops"


def test_compute_design_readiness_score_not_zero_when_execution_blocked():
    score = compute_design_readiness_score(
        {"score": 0.95},
        [{"coverage": "full"}] * 10,
        {"scenarios": [{}] * 136},
    )
    assert score >= 80.0


def test_run_0113cfc5e3bd_artifact_regression():
    repo = Path(__file__).resolve().parents[1]
    summary_path = (
        repo
        / "generated-test-projects"
        / "DEMO-QA"
        / "aptp-artifacts"
        / "run-0113cfc5e3bd"
        / "pipeline_summary.json"
    )
    if not summary_path.is_file():
        pytest.skip("run-0113cfc5e3bd artifacts not present")

    results = json.loads(summary_path.read_text(encoding="utf-8")).get("results") or {}
    execution = results.get("execution") or {}
    ctx = validation_blocked_context(execution, results.get("rca"))
    assert ctx["validation_blocked"] is True

    _, blocker = consolidate_execution_failures(
        execution.get("failures") or [],
        rca=results.get("rca"),
        execution=execution,
    )
    assert blocker is not None
    assert blocker["affected_tests_count"] >= 32
