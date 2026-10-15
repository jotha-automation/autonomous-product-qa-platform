"""Tests for visual report builder and evidence resolution."""
import json
from pathlib import Path

import pytest

from src.automation.negative_scenarios import synthesize_negative_scenarios
from src.reporting.visual_report import (
    build_visual_report,
    evidence_asset_url,
    resolve_evidence_file,
)
from src.agents.site_exploration import SiteExplorationAgent


def test_evidence_asset_url():
    url = evidence_asset_url("run-abc", "screenshots/t1/step.png")
    assert url == "/api/v1/runs/run-abc/evidence/screenshots/t1/step.png"


def test_build_visual_report_minimal():
    summary = {
        "run_id": "run-1",
        "project_id": "proj-1",
        "status": "completed",
        "duration_seconds": 120,
        "results": {
            "test_design": {
                "functional_scenarios": [
                    {
                        "id": "TC-001",
                        "title": "Login success",
                        "scenario_type": "happy_path",
                        "priority": "P0",
                        "expected_results": ["User logged in"],
                    },
                    {
                        "id": "TC-001-invalid-password",
                        "title": "Login — invalid password",
                        "scenario_type": "negative",
                        "priority": "P1",
                        "playwright_skip": True,
                        "parent_scenario_id": "TC-001",
                    },
                ]
            },
            "executive_report": {
                "executive_summary": "All good",
                "release_readiness_score": 85.5,
            },
        },
    }
    reports = {
        "executive": {
            "executive_summary": "All good",
            "release_readiness_score": 85.5,
            "technical_summary": "Technical details",
        },
        "automation": {
            "execution": {
                "status": "passed",
                "passed": 2,
                "failed": 0,
                "total": 2,
                "pass_rate": 1.0,
                "executed_tests": [
                    {"id": "TC-001", "title": "Login success", "status": "passed", "duration_ms": 1000},
                ],
            },
            "generated_code": {"file_count": 3, "files": ["tests/login.spec.ts"]},
        },
        "defects": {"total": 0, "defects": [], "blocking": 0},
        "deliverable": {
            "validation": {"valid": True, "issues": []},
            "obligation_rows": [{"title": "Login", "result": "Pass"}],
        },
    }
    visual = build_visual_report("run-1", summary, reports=reports)
    assert visual["type"] == "visual"
    assert visual["executive"]["release_readiness_score"] == 85.5
    assert visual["executive"]["tests_passed"] == 2
    assert len(visual["test_cases"]) == 2
    executed_cases = [c for c in visual["test_cases"] if c.get("executed")]
    assert len(executed_cases) == 1
    assert executed_cases[0]["status"] == "passed"
    design_cases = [c for c in visual["test_cases"] if not c.get("executed")]
    assert len(design_cases) == 1
    assert design_cases[0]["status"] == "skipped"
    assert visual["executive"]["scenario_coverage"]["happy_path"] == 1
    assert visual["executive"]["design_scenario_coverage"]["negative"] == 1
    assert visual["deliverable"]["download_url"].endswith("deliverable.xlsx")


def test_synthesize_negative_scenarios_expands_base():
    base = [
        {
            "id": "OBL-1",
            "title": "User login",
            "steps": ["Open login", "Submit credentials"],
            "expected_results": ["Dashboard visible"],
        }
    ]
    site = {
        "negative_opportunities": [
            {
                "id": "extra-1",
                "flow": "login",
                "title": "Brute force lockout",
                "scenario_type": "resilience",
                "input_variation": "rate_limit",
                "expected_failure_mode": "validation_error",
            }
        ]
    }
    expanded = synthesize_negative_scenarios(base, site_exploration=site, max_per_base=2)
    assert len(expanded) > 1
    types = {s.get("scenario_type") for s in expanded}
    assert "happy_path" in types
    assert "negative" in types or "resilience" in types


def test_resolve_evidence_file_sandbox(tmp_path, monkeypatch):
    run_id = "run-evidence"
    artifact_dir = tmp_path / "proj" / "aptp-artifacts" / run_id
    evidence = artifact_dir / "evidence" / "screenshots" / "t1" / "step.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"png")

    from src.services import artifact_bundle

    monkeypatch.setattr(artifact_bundle, "find_artifact_dir_on_disk", lambda _: artifact_dir)

    resolved = resolve_evidence_file(run_id, "screenshots/t1/step.png")
    assert resolved is not None
    assert resolved.is_file()

    assert resolve_evidence_file(run_id, "../secrets.txt") is None


def test_build_visual_report_no_name_error_with_obligations_only():
    """Regression: build_visual_report must not raise NameError when only obligations exist."""
    summary = {
        "run_id": "run-2",
        "project_id": "proj-2",
        "status": "completed",
        "results": {
            "reports": {
                "qa": {
                    "interpreted_intake": {
                        "obligations": [
                            {"id": "OBL-001", "title": "Login flow", "automation_tier": "automated"}
                        ],
                        "deliverable_metadata": {"inc_id": "INC1353166"},
                    }
                }
            },
            "test_design": {"functional_scenarios": []},
            "executive_report": {"executive_summary": "OK"},
        },
    }
    visual = build_visual_report("run-2", summary)
    assert visual["type"] == "visual"
    assert visual["executive"]["tests_total"] == 0
    assert visual["deliverable"]["inc"] == "INC1353166"


@pytest.mark.asyncio
async def test_site_exploration_bank_portal():
    agent = SiteExplorationAgent.__new__(SiteExplorationAgent)
    result = await agent._bank_exploration("http://localhost:3000")
    assert result["bank_portal"] is True
    assert len(result["routes"]) >= 5
    assert all(isinstance(r, dict) and "path" in r and "name" in r for r in result["routes"])
    assert len(result["negative_opportunities"]) >= 4
