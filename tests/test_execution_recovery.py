"""Tests for Playwright execution recovery from on-disk artifacts."""
import json
from pathlib import Path

from src.reporting.execution_recovery import (
    evidence_key_from_title,
    obligation_id_from_title,
    recover_execution_from_artifacts,
    resolve_playwright_project_dir,
)
from src.reporting.visual_report import build_visual_report


def test_obligation_id_from_playwright_title():
    title = "OBL-EMP-12975: empresarial-12975:-parámetro-=-2-días-hábiles"
    assert obligation_id_from_title(title) == "OBL-EMP-12975"
    assert evidence_key_from_title(title).startswith("OBL-EMP-12975")


def test_recover_execution_from_demo_qa_results():
    repo = Path(__file__).resolve().parents[1]
    run_id = "run-c7a74d6b2633"
    summary_path = (
        repo
        / "generated-test-projects"
        / "DEMO-QA"
        / "aptp-artifacts"
        / run_id
        / "pipeline_summary.json"
    )
    if not summary_path.is_file():
        return

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    results = summary.get("results") or {}
    execution = dict(results.get("execution") or {})
    execution["executed_tests"] = []
    execution["passed"] = 0
    execution["failed"] = 1

    recovered = recover_execution_from_artifacts(
        execution,
        run_id=run_id,
        summary=summary,
        reports={"automation": {"generated_code": results.get("generated_code") or {}}},
    )
    assert recovered.get("recovered_from_disk") is True
    assert len(recovered.get("executed_tests") or []) >= 1
    assert int(recovered.get("total") or 0) >= 1
    assert int(recovered.get("failed") or 0) >= 1


def test_build_visual_report_recovers_run_c7a74d6b2633():
    repo = Path(__file__).resolve().parents[1]
    run_id = "run-c7a74d6b2633"
    summary_path = (
        repo
        / "generated-test-projects"
        / "DEMO-QA"
        / "aptp-artifacts"
        / run_id
        / "pipeline_summary.json"
    )
    manifest_path = (
        repo
        / "generated-test-projects"
        / "DEMO-QA"
        / "aptp-artifacts"
        / run_id
        / "evidence"
        / "evidence-manifest.json"
    )
    if not summary_path.is_file():
        return

    summary = {
        "run_id": run_id,
        "project_id": summary_path.parent.parent.parent.name,
        "status": "failed",
        "duration_seconds": 600,
        "results": json.loads(summary_path.read_text(encoding="utf-8")).get("results") or {},
    }
    evidence_manifest = {}
    if manifest_path.is_file():
        evidence_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    project_dir = resolve_playwright_project_dir(run_id, summary)
    assert project_dir is not None

    visual = build_visual_report(run_id, summary, evidence_manifest=evidence_manifest)
    executed = [c for c in visual["test_cases"] if c.get("executed")]
    assert len(executed) >= 1
    assert visual["executive"]["tests_total"] >= 1
    if visual["executive"].get("infrastructure_blocker"):
        assert visual["executive"]["tests_blocked"] >= 1
    else:
        assert visual["executive"]["tests_failed"] >= 1
    assert any(c.get("status") == "passed" for c in executed) or visual["executive"].get("infrastructure_blocker")
    assert any(c.get("status") in {"failed", "blocked"} for c in executed)
    assert visual["evidence"]["groups"]


def test_recover_execution_from_disk_artifacts(tmp_path):
    """Regression: recover executed_tests and counts from on-disk Playwright JSON."""
    run_id = "run-disk-test"
    project_dir = tmp_path / "pw-project"
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True)
    (project_dir / "playwright.config.ts").write_text("export default {};\n", encoding="utf-8")
    report = {
        "stats": {"expected": 1, "unexpected": 1, "skipped": 0, "duration": 3000},
        "suites": [
            {
                "title": "bank-portal",
                "specs": [
                    {
                        "title": "OBL-EMP-001: crear-digitar",
                        "tests": [
                            {"status": "passed", "results": [{"status": "passed", "duration": 1000}]}
                        ],
                    },
                    {
                        "title": "OBL-EMP-002: validar",
                        "tests": [
                            {
                                "status": "failed",
                                "results": [
                                    {
                                        "status": "failed",
                                        "duration": 500,
                                        "error": {"message": "Timeout waiting for locator"},
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
    }
    (reports_dir / "results.json").write_text(json.dumps(report), encoding="utf-8")

    summary = {
        "run_id": run_id,
        "project_id": "disk-proj",
        "status": "failed",
    }
    reports = {
        "automation": {
            "generated_code": {"project_path": str(project_dir)},
        }
    }
    recovered = recover_execution_from_artifacts(
        {}, run_id=run_id, summary=summary, reports=reports, evidence_manifest={}
    )
    assert recovered.get("recovered_from_disk") is True
    assert recovered["passed"] == 1
    assert recovered["failed"] == 1
    assert recovered["total"] == 2
    assert len(recovered["executed_tests"]) == 2
    assert any(t.get("status") == "passed" for t in recovered["executed_tests"])
    assert any(t.get("status") == "failed" for t in recovered["executed_tests"])


def test_ansi_codes_stripped_from_execution_errors():
    from src.reporting.execution_recovery import _strip_ansi

    ansi_text = "\x1b[31mError:\x1b[0m timeout"
    assert _strip_ansi(ansi_text) == "Error: timeout"
