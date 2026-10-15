"""Release readiness score and report builders."""
from typing import Any, Dict, List, Optional

from src.runtime_policy import data_provenance, ensure_not_mock_execution_result
from src.reporting.lists import coerce_string_list
from src.reporting.numeric import coerce_score_float
from src.reporting.visual_report import build_visual_report, load_evidence_manifest_from_disk


def _obligation_coverage_ratio(coverage: Dict[str, Any]) -> Optional[float]:
    obligation_coverage = coverage.get("obligation_coverage")
    if not isinstance(obligation_coverage, list) or not obligation_coverage:
        return None
    covered = sum(
        1 for item in obligation_coverage if isinstance(item, dict) and item.get("covered")
    )
    return covered / len(obligation_coverage)


def _resolve_coverage_score(coverage: Dict[str, Any]) -> float:
    coverage_score = coerce_score_float(coverage.get("coverage_score"), -1.0)
    if coverage_score > 0:
        return max(0.0, min(1.0, coverage_score))
    ratio = _obligation_coverage_ratio(coverage)
    if ratio is not None:
        return max(0.0, min(1.0, ratio))
    if coverage.get("obligation_coverage") is not None:
        return max(0.0, min(1.0, coerce_score_float(coverage.get("obligation_coverage"), 0.5)))
    return 0.5


def _resolve_risk_raw(risk: Dict[str, Any]) -> Any:
    risk_scores = risk.get("risk_scores") if isinstance(risk.get("risk_scores"), dict) else {}
    return (
        risk.get("release_risk_score")
        or risk_scores.get("functional")
        or risk_scores.get("funcional")
        or 0.3
    )


def compute_release_readiness_score(
    coverage: Dict[str, Any],
    risk: Dict[str, Any],
    traceability: List[Dict[str, Any]],
    readiness: Dict[str, Any],
    *,
    execution: Optional[Dict[str, Any]] = None,
) -> float:
    coverage_score = _resolve_coverage_score(coverage)
    risk_score = max(0.2, 1.0 - coerce_score_float(_resolve_risk_raw(risk), 0.3))
    req_quality = coerce_score_float(readiness.get("score", 0.5))
    if traceability:
        traced_weight = sum(
            1.0
            if t.get("coverage") in ("full", "partial")
            else 0.5
            if t.get("coverage") == "manual"
            else 0.0
            for t in traceability
        )
        trace_score = traced_weight / max(len(traceability), 1)
    else:
        trace_score = 0.75
    normalized = (
        max(0.0, min(1.0, coverage_score))
        * max(0.0, min(1.0, risk_score))
        * max(0.0, min(1.0, req_quality))
        * max(0.0, min(1.0, trace_score))
    )
    score = round(normalized * 100, 2)
    if execution:
        total = int(execution.get("total") or 0)
        failed = int(execution.get("failed") or 0)
        if total > 0:
            pass_rate = coerce_score_float(
                execution.get("pass_rate"),
                (total - failed) / total,
            )
            exec_score = round(pass_rate * 100, 2)
            exec_weight = 0.7 if failed == 0 else 0.85
            score = max(score, round(exec_score * exec_weight + score * (1 - exec_weight), 2))
    return score


def _report_meta(results: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "data_provenance": results.get("data_provenance") or data_provenance(),
        "ai_generated": True,
    }


def build_qa_report(results: Dict[str, Any]) -> Dict[str, Any]:
    trace = results.get("traceability") or {}
    matrix = trace.get("traceability_matrix") or trace.get("matrix") or []
    interpreted = dict(results.get("interpreted_intake") or {})
    readiness = results.get("readiness") or {}
    if not interpreted.get("intake_profile") and readiness.get("intake_profile"):
        interpreted["intake_profile"] = readiness["intake_profile"]
    if not interpreted.get("obligation_count") and readiness.get("obligation_count"):
        interpreted["obligation_count"] = readiness["obligation_count"]
    safe_intake = _redact_interpreted_intake(interpreted)
    return {
        "type": "qa",
        **_report_meta(results),
        "readiness": results.get("readiness"),
        "coverage": results.get("coverage"),
        "traceability": {"matrix": matrix, "count": len(matrix)},
        "test_design": results.get("test_design"),
        "interpreted_intake": safe_intake,
        "functional_scenario_count": len(
            (results.get("test_design") or {}).get("functional_scenarios") or []
        ),
    }


def _redact_interpreted_intake(interpreted: Dict[str, Any]) -> Dict[str, Any]:
    if not interpreted:
        return {}
    assets = []
    for asset in interpreted.get("assets") or []:
        safe = dict(asset)
        safe["fields"] = {k: "***" if asset.get("sensitive") else v for k, v in (asset.get("fields") or {}).items()}
        assets.append(safe)
    obligations = interpreted.get("obligations") or []
    return {
        "intake_mode": interpreted.get("intake_mode"),
        "confidence": interpreted.get("confidence"),
        "environment": interpreted.get("environment"),
        "intake_profile": interpreted.get("intake_profile"),
        "obligations": obligations,
        "obligation_count": interpreted.get("obligation_count") or len(obligations),
        "assets": assets,
        "asset_bindings": interpreted.get("asset_bindings") or [],
        "deliverable_metadata": interpreted.get("deliverable_metadata"),
        "interpretation_notes": interpreted.get("interpretation_notes"),
    }


def build_automation_report(results: Dict[str, Any]) -> Dict[str, Any]:
    execution = results.get("execution") or {}
    ensure_not_mock_execution_result(execution)
    generated = results.get("generated_code") or {}
    return {
        "type": "automation",
        **_report_meta(results),
        "test_plan": results.get("test_plan"),
        "generated_code": {
            "project_path": generated.get("project_path"),
            "files": generated.get("files", []),
            "generated_tests": generated.get("generated_tests", []),
            "file_count": len(generated.get("files") or []),
        },
        "execution": {
            "status": execution.get("status"),
            "mode": execution.get("mode"),
            "passed": execution.get("passed", 0),
            "failed": execution.get("failed", 0),
            "total": execution.get("total", 0),
            "pass_rate": execution.get("pass_rate", 0),
            "executed_tests": execution.get("executed_tests", []),
            "artifacts": execution.get("artifacts"),
            "evidence": execution.get("evidence"),
            "infra_error": execution.get("infra_error", False),
            "error_category": execution.get("error_category"),
            "error": execution.get("error"),
        },
        "heal_attempts": results.get("heal_attempts", 0),
    }


def build_rca_report(results: Dict[str, Any]) -> Dict[str, Any]:
    rca = results.get("rca") or {}
    return {
        "type": "rca",
        **_report_meta(results),
        "rca": rca,
        "has_analysis": bool(rca.get("root_cause") or rca.get("likely_root_cause")),
    }


def build_healing_report(results: Dict[str, Any]) -> Dict[str, Any]:
    healing = results.get("healing") or {}
    return {
        "type": "healing",
        **_report_meta(results),
        "healing": healing,
        "healing_success_pct": 100.0 if healing.get("healed") else 0.0,
        "changes_applied": len(healing.get("changes_applied") or []),
    }


def build_defects_report(results: Dict[str, Any]) -> Dict[str, Any]:
    defects = results.get("defects") or {}
    return {
        "type": "defects",
        **_report_meta(results),
        **defects,
    }


def _traceability_list(traceability: Any) -> List[Dict[str, Any]]:
    if isinstance(traceability, list):
        return traceability
    if isinstance(traceability, dict):
        return traceability.get("traceability_matrix") or traceability.get("matrix") or []
    return []


def _refresh_executive_readiness_scores(results: Dict[str, Any], exec_r: Dict[str, Any]) -> Dict[str, Any]:
    execution = results.get("execution") or {}
    total = int(execution.get("total") or 0)
    if total <= 0:
        return exec_r
    refreshed = dict(exec_r)
    trace_list = _traceability_list(results.get("traceability"))
    release_score = compute_release_readiness_score(
        results.get("coverage") or {},
        results.get("risk") or {},
        trace_list,
        results.get("readiness") or {},
        execution=execution,
    )
    refreshed["execution_informed_score"] = release_score
    if refreshed.get("validation_blocked") and refreshed.get("design_readiness_score") is not None:
        refreshed["release_readiness_score"] = refreshed["design_readiness_score"]
    else:
        refreshed["release_readiness_score"] = release_score
    return refreshed


def build_executive_report(results: Dict[str, Any]) -> Dict[str, Any]:
    exec_r = _refresh_executive_readiness_scores(results, dict(results.get("executive_report") or {}))
    exec_r["recommendations"] = coerce_string_list(exec_r.get("recommendations"))
    execution = results.get("execution") or {}
    ensure_not_mock_execution_result(execution)
    tests_executed = execution.get("total", 0)
    if not tests_executed:
        tests_executed = len(execution.get("executed_tests") or [])
    return {
        "type": "executive",
        **_report_meta(results),
        **exec_r,
        "automation_summary": {
            "tests_executed": tests_executed,
            "pass_rate": execution.get("pass_rate", 0),
            "playwright_project": (results.get("generated_code") or {}).get("project_path"),
            "execution_status": execution.get("status"),
            "compile_errors": execution.get("compile_errors", 0),
        },
        "defects_total": (results.get("defects") or {}).get("total", 0),
    }


def build_visual_report_for_run(
    run_id: str,
    summary: Dict[str, Any],
    *,
    reports: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build normalized visual report for UI dashboards."""
    results = summary.get("results") or {}
    reports = reports or results.get("reports") or build_all_reports(results, include_visual=False)
    evidence_manifest = load_evidence_manifest_from_disk(run_id)
    if not evidence_manifest:
        execution = results.get("execution") or {}
        evidence = execution.get("evidence") or {}
        manifest_path = evidence.get("manifest_path")
        if manifest_path:
            from pathlib import Path
            import json

            p = Path(manifest_path)
            if p.is_file():
                try:
                    evidence_manifest = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    evidence_manifest = {}
    manifest: Dict[str, Any] = {}
    try:
        from src.services.artifact_bundle import get_run_manifest

        manifest = get_run_manifest(run_id)
    except (FileNotFoundError, OSError):
        pass
    return build_visual_report(
        run_id,
        summary,
        reports=reports,
        evidence_manifest=evidence_manifest,
        manifest=manifest,
    )


def enrich_reports_from_results(
    reports: Dict[str, Any],
    results: Dict[str, Any],
    run_id: str,
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    """Refresh derived UI fields for cached report bundles (intake counts, readiness scores)."""
    enriched = dict(reports)
    enriched["qa"] = build_qa_report(results)
    enriched["executive"] = build_executive_report(results)
    if enriched.get("visual") or results.get("execution"):
        try:
            enriched["visual"] = build_visual_report_for_run(run_id, summary, reports=enriched)
        except Exception:
            if "visual" in enriched:
                visual = dict(enriched["visual"])
                executive = visual.get("executive") or {}
                visual["executive"] = {
                    **executive,
                    "release_readiness_score": enriched["executive"].get("release_readiness_score"),
                    "design_readiness_score": enriched["executive"].get("design_readiness_score"),
                }
                enriched["visual"] = visual
    return enriched


def build_all_reports(results: Dict[str, Any], *, include_visual: bool = True) -> Dict[str, Any]:
    reports: Dict[str, Any] = {}
    for key, builder in (
        ("qa", build_qa_report),
        ("automation", build_automation_report),
        ("rca", build_rca_report),
        ("healing", build_healing_report),
        ("defects", build_defects_report),
        ("executive", build_executive_report),
    ):
        try:
            reports[key] = builder(results)
        except Exception:
            execution = results.get("execution") or {}
            if key == "automation" and execution:
                reports[key] = {
                    "type": "automation",
                    **_report_meta(results),
                    "execution": {
                        "status": execution.get("status"),
                        "mode": execution.get("mode"),
                        "passed": execution.get("passed", 0),
                        "failed": execution.get("failed", 0),
                        "total": execution.get("total", 0),
                        "infra_error": execution.get("infra_error", False),
                        "error": execution.get("error"),
                    },
                }
            elif key != "executive":
                continue
    deliverable = results.get("deliverable") or {}
    if deliverable.get("deliverable_path"):
        execution = results.get("execution") or {}
        reports["deliverable"] = {
            "type": "deliverable",
            "path": deliverable.get("deliverable_path"),
            "validation": deliverable.get("validation"),
            "obligation_rows": deliverable.get("obligation_rows") or deliverable.get("rows"),
            "execution_passed": execution.get("passed"),
            "execution_failed": execution.get("failed"),
            "execution_status": execution.get("status"),
        }
    if include_visual:
        run_id = results.get("run_id") or ""
        if run_id:
            summary = {
                "run_id": run_id,
                "project_id": results.get("project_id"),
                "status": results.get("pipeline_status", "completed"),
                "duration_seconds": results.get("duration_seconds"),
                "results": results,
            }
            try:
                reports["visual"] = build_visual_report_for_run(run_id, summary, reports=reports)
            except Exception:
                pass
    return reports
