"""Recover Playwright execution metrics when pipeline parsing failed or artifacts are partial."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.evidence_collector import _safe_segment


_OBLIGATION_ID_RE = re.compile(r"(OBL-[A-Z]+-\d+)", re.IGNORECASE)


def obligation_id_from_title(title: str) -> Optional[str]:
    match = _OBLIGATION_ID_RE.search(title or "")
    return match.group(1).upper() if match else None


def evidence_key_from_title(title: str) -> str:
    return _safe_segment(title or "unknown")


def resolve_playwright_project_dir(
    run_id: str,
    summary: Dict[str, Any],
    reports: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Locate the generated Playwright project directory for a run."""
    from src.services.artifact_bundle import GENERATED_PROJECTS_ROOT, find_artifact_dir_on_disk

    results = summary.get("results") or {}
    reports = reports or {}
    automation = reports.get("automation") or {}
    generated = automation.get("generated_code") or results.get("generated_code") or {}
    project_path = generated.get("project_path")
    if project_path:
        path = Path(project_path)
        if path.is_dir():
            return path

    execution = results.get("execution") or automation.get("execution") or {}
    results_file = execution.get("results_file")
    if results_file:
        candidate = Path(results_file).parent.parent
        if candidate.is_dir() and (candidate / "playwright.config.ts").is_file():
            return candidate

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if artifact_dir is not None:
        project = artifact_dir.parent.parent
        if project.is_dir():
            return project

    stored = summary.get("artifact_dir")
    if stored:
        artifact = Path(stored)
        if artifact.is_dir() and artifact.parent.name == "aptp-artifacts":
            project = artifact.parent.parent
            if project.is_dir():
                return project

    project_id = summary.get("project_id")
    if project_id:
        fallback = GENERATED_PROJECTS_ROOT / project_id
        if fallback.is_dir():
            return fallback
    return None


def resolve_results_json_path(
    run_id: str,
    summary: Dict[str, Any],
    reports: Optional[Dict[str, Any]] = None,
    execution: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    execution = execution or {}
    results_file = execution.get("results_file")
    if results_file:
        path = Path(results_file)
        if path.is_file():
            return path

    project_dir = resolve_playwright_project_dir(run_id, summary, reports)
    if project_dir is not None:
        candidate = project_dir / "reports" / "results.json"
        if candidate.is_file():
            return candidate
    return None


def _strip_ansi(text: str) -> str:
    from src.reporting.error_sanitization import strip_ansi as _strip

    return _strip(text)


def parse_executed_tests_from_report(report: Dict[str, Any], project_dir: Path) -> List[Dict[str, Any]]:
    from src.agents.execution import ExecutionAgent

    agent = ExecutionAgent.__new__(ExecutionAgent)
    tests = agent._extract_executed_tests(report, project_dir)
    for test in tests:
        title = _strip_ansi(str(test.get("title") or ""))
        test["title"] = title
        error = test.get("error")
        if error:
            from src.reporting.error_sanitization import sanitize_playwright_error

            test["error"] = sanitize_playwright_error(_strip_ansi(str(error)))
        test["obligation_id"] = obligation_id_from_title(title)
        test["evidence_key"] = evidence_key_from_title(title)
        if test.get("obligation_id") and str(test.get("id", "")).startswith("TC-"):
            test["id"] = test["obligation_id"]
    return tests


def execution_metrics_from_report(report: Dict[str, Any], executed_tests: List[Dict[str, Any]]) -> Dict[str, Any]:
    stats = report.get("stats") or {}
    passed = int(stats.get("expected") or 0)
    failed = int(stats.get("unexpected") or 0)
    total = passed + failed
    if total == 0 and executed_tests:
        passed = sum(1 for t in executed_tests if t.get("status") == "passed")
        failed = sum(1 for t in executed_tests if t.get("status") == "failed")
        total = len(executed_tests)
    return {
        "passed": passed,
        "failed": failed,
        "total": total,
        "pass_rate": passed / total if total else 0.0,
        "duration_seconds": float(stats.get("duration") or 0) / 1000.0,
        "status": "completed" if failed == 0 and total else "failed",
    }


def recover_execution_from_artifacts(
    execution: Dict[str, Any],
    *,
    run_id: str,
    summary: Dict[str, Any],
    reports: Optional[Dict[str, Any]] = None,
    evidence_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fill missing executed_tests and counts from on-disk Playwright JSON when possible."""
    execution = dict(execution or {})
    executed_tests = execution.get("executed_tests") or []
    has_counts = int(execution.get("total") or 0) > 0 or (
        int(execution.get("passed") or 0) + int(execution.get("failed") or 0) > 0
    )
    if executed_tests and has_counts:
        return execution

    report_path = resolve_results_json_path(run_id, summary, reports, execution)
    if report_path is None:
        return _recover_from_evidence_manifest(execution, evidence_manifest)

    project_dir = report_path.parent.parent
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _recover_from_evidence_manifest(execution, evidence_manifest)

    parsed_tests = parse_executed_tests_from_report(report, project_dir)
    if not parsed_tests:
        return _recover_from_evidence_manifest(execution, evidence_manifest)

    metrics = execution_metrics_from_report(report, parsed_tests)
    recovered = {**execution, **metrics, "executed_tests": parsed_tests, "recovered_from_disk": True}
    recovered["results_file"] = str(report_path)

    from src.services.evidence_collector import _attach_evidence_refs

    manifest_entries = list((evidence_manifest or {}).get("entries") or [])
    if manifest_entries:
        _attach_evidence_refs(recovered, manifest_entries)
    return recovered


def _recover_from_evidence_manifest(
    execution: Dict[str, Any],
    evidence_manifest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    manifest = evidence_manifest or {}
    entries = manifest.get("entries") or []
    if not entries:
        return execution

    execution = dict(execution)
    tests_by_key: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        media_type = entry.get("media_type")
        if media_type not in {"screenshot", "video", "image"}:
            continue
        test_id = str(entry.get("test_id") or "")
        if not test_id or test_id in {"failure", "compile", "general"}:
            continue
        title = str(entry.get("test_title") or test_id)
        tests_by_key.setdefault(
            test_id,
            {
                "id": obligation_id_from_title(title) or test_id,
                "title": title,
                "evidence_key": test_id,
                "obligation_id": obligation_id_from_title(title),
                "status": "unknown",
                "duration_ms": None,
            },
        )

    if not tests_by_key:
        return execution

    passed = int(manifest.get("tests_passed") or 0)
    failed = int(manifest.get("tests_failed") or 0)
    total = passed + failed or len(tests_by_key)
    execution.update(
        {
            "executed_tests": list(tests_by_key.values()),
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": passed / total if total else 0.0,
            "recovered_from_manifest": True,
        }
    )
    return execution
