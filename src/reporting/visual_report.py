"""Normalized visual report contract for executive and technical UI dashboards."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.intake.test_naming import assign_case_codes, automation_tier_label_es, format_case_code
from src.reporting.lists import coerce_string_list
from src.reporting.execution_recovery import (
    evidence_key_from_title,
    obligation_id_from_title,
    recover_execution_from_artifacts,
)
from src.reporting.error_sanitization import (
    apply_infrastructure_execution_semantics,
    consolidate_infrastructure_defects,
    detect_execution_infrastructure_blocker,
    is_infrastructure_failure,
    sanitize_playwright_error,
    validation_blocked_context,
)

SCENARIO_TYPES = (
    "happy_path",
    "negative",
    "boundary",
    "resilience",
    "security",
    "accessibility",
)


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def evidence_asset_url(run_id: str, relative_path: str) -> str:
    rel = (relative_path or "").lstrip("/")
    return f"/api/v1/runs/{run_id}/evidence/{rel}"


def _infer_scenario_type(scenario: Dict[str, Any]) -> str:
    explicit = (scenario.get("scenario_type") or "").lower()
    if explicit in SCENARIO_TYPES:
        return explicit
    test_types = [str(t).lower() for t in scenario.get("test_types") or []]
    if "negative" in test_types or scenario.get("negative_paths"):
        return "negative"
    if "boundary" in test_types:
        return "boundary"
    if "resilience" in test_types:
        return "resilience"
    if "security" in test_types:
        return "security"
    title = (scenario.get("title") or scenario.get("name") or "").lower()
    if any(k in title for k in ("invalid", "fail", "error", "reject", "unauthorized", "negativo")):
        return "negative"
    return "happy_path"


def _severity_counts(defects: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for defect in defects:
        sev = str(defect.get("severity") or "unknown").lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _rca_categories(rca: Dict[str, Any]) -> Dict[str, int]:
    categories: Dict[str, int] = {}
    for item in _coerce_list(rca.get("failure_analyses") or rca.get("analyses")):
        if not isinstance(item, dict):
            continue
        cat = str(item.get("failure_category") or item.get("category") or "unknown")
        categories[cat] = categories.get(cat, 0) + 1
    root = rca.get("failure_category") or rca.get("category")
    if root and not categories:
        categories[str(root)] = 1
    return categories


def _build_evidence_assets(
    run_id: str,
    evidence_manifest: Dict[str, Any],
    automation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for entry in _coerce_list(evidence_manifest.get("entries")):
        if not isinstance(entry, dict):
            continue
        rel = str(entry.get("path") or "")
        if not rel or rel in seen:
            continue
        seen.add(rel)
        assets.append(
            {
                "id": f"{entry.get('test_id', '')}:{entry.get('step_id', '')}",
                "test_id": entry.get("test_id"),
                "test_title": entry.get("test_title"),
                "step_id": entry.get("step_id"),
                "label": entry.get("label"),
                "media_type": entry.get("media_type", "screenshot"),
                "status": entry.get("status"),
                "relative_path": rel,
                "url": evidence_asset_url(run_id, rel),
            }
        )

    exec_evidence = _coerce_dict((automation.get("execution") or {}).get("evidence"))
    pdf_name = "execution-report.pdf"
    html_rel = "evidence/html/index.html"
    assets.append(
        {
            "id": "pdf-report",
            "test_id": None,
            "test_title": "Execution PDF",
            "step_id": "pdf",
            "label": "Execution report (PDF)",
            "media_type": "pdf",
            "status": "available" if exec_evidence.get("pdf_path") else "missing",
            "relative_path": pdf_name,
            "url": f"/api/v1/runs/{run_id}/evidence/{pdf_name}",
        }
    )
    if exec_evidence.get("html_report_path"):
        assets.append(
            {
                "id": "html-report",
                "test_id": None,
                "test_title": "Playwright HTML report",
                "step_id": "html",
                "label": "HTML execution report",
                "media_type": "html",
                "status": "available",
                "relative_path": html_rel,
                "url": f"/api/v1/runs/{run_id}/evidence/{html_rel}",
            }
        )
    return assets


def _normalize_execution_status(status: Any) -> str:
    raw = str(status or "").lower()
    if raw in {"passed", "expected", "pass"}:
        return "passed"
    if raw in {"failed", "unexpected", "error", "fail"}:
        return "failed"
    if raw == "skipped":
        return "skipped"
    if raw in {"unknown", "not_executed", "not executed", "pending", ""}:
        return "not_executed"
    if raw == "blocked":
        return "blocked"
    return raw


def _collect_evidence_for_case(
    run_id: str,
    case_id: str,
    exec_test: Optional[Dict[str, Any]],
    evidence_assets: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    linked: List[Dict[str, Any]] = []
    seen: set[str] = set()
    exec_test = exec_test or {}
    exec_title = str(exec_test.get("title") or "")
    evidence_key = str(exec_test.get("evidence_key") or evidence_key_from_title(exec_title))
    obl_id = str(exec_test.get("obligation_id") or obligation_id_from_title(exec_title) or "")

    def _add(asset: Dict[str, Any]) -> None:
        aid = str(asset.get("id") or asset.get("relative_path") or "")
        if aid and aid not in seen:
            seen.add(aid)
            linked.append(asset)

    for asset in evidence_assets:
        tid = str(asset.get("test_id") or "")
        ttitle = str(asset.get("test_title") or "")
        if not tid:
            continue
        if (
            tid == case_id
            or tid == evidence_key
            or tid == exec_title
            or (obl_id and obl_id in tid)
            or case_id in tid
            or tid in case_id
            or (exec_title and exec_title.casefold() in ttitle.casefold())
            or (evidence_key and evidence_key in tid)
        ):
            _add(asset)

    if exec_test and exec_test.get("evidence_refs"):
        for ref in exec_test["evidence_refs"]:
            if not isinstance(ref, dict):
                continue
            rel = ref.get("screenshot_path") or ref.get("path") or ""
            _add(
                {
                    "id": f"{case_id}:{ref.get('step_id', '')}",
                    "test_id": case_id,
                    "step_id": ref.get("step_id"),
                    "label": ref.get("label"),
                    "media_type": ref.get("media_type", "screenshot"),
                    "relative_path": rel,
                    "url": evidence_asset_url(run_id, rel) if rel else None,
                }
            )
    return linked[:24]


def _find_scenario_for_executed_test(
    test: Dict[str, Any],
    scenarios: List[Dict[str, Any]],
    plan_scenarios: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    all_scenarios = [s for s in scenarios + plan_scenarios if isinstance(s, dict)]
    test_id = str(test.get("id") or "")
    test_title = str(test.get("title") or test.get("name") or "")
    test_slug = _slug(test_title)
    obl_id = str(test.get("obligation_id") or obligation_id_from_title(test_title) or "")

    if obl_id:
        for sc in all_scenarios:
            if str(sc.get("id") or "").upper() == obl_id.upper():
                return sc
            if obl_id.upper() in str(sc.get("external_id") or "").upper():
                return sc

    for sc in all_scenarios:
        if str(sc.get("id") or "") == test_id:
            return sc
    title_key = test_title.casefold()
    for sc in all_scenarios:
        sc_title = str(sc.get("title") or sc.get("name") or "").casefold()
        if sc_title and sc_title == title_key:
            return sc
    for sc in all_scenarios:
        sc_slug = _slug(str(sc.get("title") or sc.get("name") or ""))
        if sc_slug and test_slug and (sc_slug in test_slug or test_slug in sc_slug):
            return sc
    emp = re.search(r"(\d{4,})", test_title)
    if emp:
        token = emp.group(1)
        file_slug = _slug(str(test.get("file") or ""))
        best: Optional[Dict[str, Any]] = None
        for sc in all_scenarios:
            sid = str(sc.get("id") or "")
            cp_code = str(sc.get("cp_code") or sc.get("name") or "")
            cp_slug = _slug(cp_code)
            if cp_slug and cp_slug in file_slug:
                return sc
            if sc.get("playwright_skip") or sc.get("parent_scenario_id"):
                continue
            blob = f"{sid} {sc.get('external_id', '')} {sc.get('title', '')}"
            if token in blob:
                if best is None:
                    best = sc
                elif len(sid) < len(str(best.get("id") or "")):
                    best = sc
        return best
    return None


def _build_test_cases(
    run_id: str,
    results: Dict[str, Any],
    reports: Dict[str, Any],
    evidence_assets: List[Dict[str, Any]],
    *,
    interpreted_obligations: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    automation = _coerce_dict(reports.get("automation"))
    execution = _coerce_dict(automation.get("execution"))
    executed_list = _coerce_list(execution.get("executed_tests"))
    executed: Dict[str, Dict[str, Any]] = {}
    for t in executed_list:
        if not isinstance(t, dict):
            continue
        for key in (t.get("id"), t.get("title"), t.get("name")):
            if key:
                executed[str(key)] = t
    defects = _coerce_list(_coerce_dict(reports.get("defects")).get("defects"))
    scenarios = _coerce_list(
        _coerce_dict(results.get("test_design")).get("functional_scenarios")
    )
    plan_scenarios = _coerce_list(_coerce_dict(results.get("test_plan")).get("scenarios"))
    generated = _coerce_dict(results.get("generated_code"))
    codegen = _coerce_dict(generated.get("codegen"))
    golden_id = generated.get("golden_template") or codegen.get("golden_template")
    golden_scope = bool(golden_id) and plan_scenarios
    design_catalog = plan_scenarios if golden_scope else scenarios
    deliverable_rows = _coerce_list(
        _coerce_dict(reports.get("deliverable")).get("obligation_rows")
    )
    row_by_ob_id = {
        str(r.get("obligation_id", "")): r for r in deliverable_rows if isinstance(r, dict) and r.get("obligation_id")
    }
    row_by_title = {
        str(r.get("title", "")).casefold(): r for r in deliverable_rows if isinstance(r, dict)
    }

    cases: List[Dict[str, Any]] = []
    matched_scenario_ids: set[str] = set()

    if executed_list:
        for test in executed_list:
            if not isinstance(test, dict):
                continue
            scenario = _find_scenario_for_executed_test(test, scenarios, plan_scenarios)
            title = str(test.get("title") or test.get("name") or "")
            sid = str(
                test.get("evidence_key")
                or evidence_key_from_title(title)
                or test.get("id")
                or title
            )
            obl_id = str(test.get("obligation_id") or obligation_id_from_title(title) or "")
            if scenario:
                matched_scenario_ids.add(str(scenario.get("id") or ""))
                if scenario.get("parent_scenario_id"):
                    matched_scenario_ids.add(str(scenario.get("parent_scenario_id")))
                if not title:
                    title = str(scenario.get("title") or scenario.get("name") or sid)
            scenario_type = _infer_scenario_type(scenario or {"title": title})
            row = row_by_ob_id.get(obl_id, {}) or row_by_title.get(title.casefold(), {})
            status = _normalize_execution_status(test.get("status"))
            linked_evidence = _collect_evidence_for_case(run_id, sid, test, evidence_assets)
            linked_defects = [
                d.get("id")
                for d in defects
                if isinstance(d, dict)
                and (sid in str(d.get("title", "")) or sid in str(d.get("description", "")))
            ]
            cases.append(
                {
                    "id": sid,
                    "cp_code": row.get("cp_code") or (scenario or {}).get("cp_code"),
                    "title": title,
                    "obligation_id": obl_id or (scenario or {}).get("id"),
                    "automation_tier": (scenario or {}).get("automation_tier") or row.get("automation_tier"),
                    "scenario_type": scenario_type,
                    "priority": (scenario or {}).get("priority", "P2"),
                    "requirement_ids": (scenario or {}).get("requirement_ids") or [],
                    "external_id": (scenario or {}).get("external_id") or obl_id,
                    "status": status,
                    "duration_ms": test.get("duration_ms"),
                    "expected_result": (
                        ((scenario or {}).get("expected_results") or [None])[0]
                        if (scenario or {}).get("expected_results")
                        else None
                    ),
                    "actual_result": row.get("result") or status,
                    "xlsx_result": row.get("result"),
                    "evidence": linked_evidence,
                    "defect_ids": linked_defects,
                    "negative_paths": (scenario or {}).get("negative_paths") or [],
                    "executed": True,
                }
            )

        for scenario in design_catalog:
            if not isinstance(scenario, dict):
                continue
            sid = str(scenario.get("id") or "")
            if sid in matched_scenario_ids:
                continue
            title = scenario.get("title") or scenario.get("name") or sid
            row = row_by_title.get(str(title).casefold(), {})
            design_only = bool(scenario.get("playwright_skip") or scenario.get("parent_scenario_id"))
            cases.append(
                {
                    "id": sid,
                    "cp_code": row.get("cp_code") or scenario.get("cp_code"),
                    "title": title,
                    "obligation_id": sid,
                    "automation_tier": scenario.get("automation_tier"),
                    "scenario_type": _infer_scenario_type(scenario),
                    "priority": scenario.get("priority", "P2"),
                    "requirement_ids": scenario.get("requirement_ids") or [],
                    "external_id": scenario.get("external_id"),
                    "status": "skipped" if design_only else "not_executed",
                    "duration_ms": None,
                    "expected_result": (
                        (scenario.get("expected_results") or [None])[0]
                        if scenario.get("expected_results")
                        else None
                    ),
                    "actual_result": "design only" if design_only else None,
                    "xlsx_result": row.get("result"),
                    "evidence": [],
                    "defect_ids": [],
                    "negative_paths": scenario.get("negative_paths") or [],
                    "executed": False,
                }
            )
        return _append_manual_obligation_cases(
            cases,
            interpreted_obligations or [],
            row_by_ob_id=row_by_ob_id,
        )

    if scenarios:
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            sid = str(scenario.get("id") or "")
            exec_test = _match_executed_test(scenario, executed)
            scenario_type = _infer_scenario_type(scenario)
            title = scenario.get("title") or scenario.get("name") or sid
            row = row_by_title.get(str(title).casefold(), {})
            linked_evidence = _collect_evidence_for_case(run_id, sid, exec_test, evidence_assets)
            linked_defects = [
                d.get("id")
                for d in defects
                if isinstance(d, dict)
                and (sid in str(d.get("title", "")) or sid in str(d.get("description", "")))
            ]
            raw_status = (exec_test or {}).get("status") if exec_test else "not_executed"
            if scenario.get("playwright_skip") and not exec_test:
                raw_status = "skipped"
            cases.append(
                {
                    "id": sid,
                    "title": title,
                    "scenario_type": scenario_type,
                    "priority": scenario.get("priority", "P2"),
                    "requirement_ids": scenario.get("requirement_ids") or [],
                    "external_id": scenario.get("external_id"),
                    "status": _normalize_execution_status(raw_status),
                    "duration_ms": (exec_test or {}).get("duration_ms"),
                    "expected_result": (
                        (scenario.get("expected_results") or [None])[0]
                        if scenario.get("expected_results")
                        else None
                    ),
                    "actual_result": row.get("result") or (exec_test or {}).get("status"),
                    "xlsx_result": row.get("result"),
                    "evidence": linked_evidence,
                    "defect_ids": linked_defects,
                    "negative_paths": scenario.get("negative_paths") or [],
                    "executed": bool(exec_test),
                }
            )
        return cases

    for test in executed_list:
        if not isinstance(test, dict):
            continue
        tid = str(test.get("id") or test.get("title", ""))
        cases.append(
            {
                "id": tid,
                "title": test.get("title", tid),
                "scenario_type": "happy_path",
                "priority": "P2",
                "requirement_ids": [],
                "status": _normalize_execution_status(test.get("status")),
                "duration_ms": test.get("duration_ms"),
                "expected_result": None,
                "actual_result": test.get("status"),
                "evidence": test.get("evidence_refs") or [],
                "defect_ids": [],
                "negative_paths": [],
                "executed": True,
            }
        )
    return _append_manual_obligation_cases(
        cases,
        interpreted_obligations or [],
        row_by_ob_id=row_by_ob_id,
    )


def _append_manual_obligation_cases(
    cases: List[Dict[str, Any]],
    obligations: List[Dict[str, Any]],
    *,
    row_by_ob_id: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not obligations:
        return cases
    coded = assign_case_codes([o for o in obligations if isinstance(o, dict)])
    seen = {str(c.get("obligation_id") or c.get("id") or "") for c in cases}
    for ob in coded:
        if ob.get("automation_tier") != "manual":
            continue
        oid = str(ob.get("id") or "")
        if oid in seen:
            continue
        row = row_by_ob_id.get(oid, {})
        cp_code = ob.get("cp_code") or format_case_code(len(cases) + 1, str(ob.get("title") or oid))
        cases.append(
            {
                "id": cp_code,
                "cp_code": cp_code,
                "title": ob.get("title") or oid,
                "obligation_id": oid,
                "automation_tier": "manual",
                "scenario_type": "happy_path",
                "priority": "P2",
                "requirement_ids": [f"REQ-{oid}"],
                "external_id": ob.get("external_id"),
                "status": "manual",
                "duration_ms": None,
                "expected_result": (ob.get("expected_results") or [None])[0] if ob.get("expected_results") else None,
                "actual_result": row.get("result") or "Manual",
                "xlsx_result": row.get("result") or "Manual",
                "evidence": [],
                "defect_ids": [],
                "negative_paths": [],
                "executed": False,
                "manual_rationale": ob.get("automation_rationale", ""),
            }
        )
        seen.add(oid)
    return cases


def _automation_breakdown(obligations: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"automated": 0, "partial": 0, "manual": 0}
    for ob in obligations:
        if not isinstance(ob, dict):
            continue
        tier = ob.get("automation_tier", "partial")
        if tier in counts:
            counts[tier] += 1
    return counts


def _slug(value: str) -> str:
    return re.sub(r"[^\w]+", "-", (value or "").casefold()).strip("-")


def _match_executed_test(
    scenario: Dict[str, Any],
    executed: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    sid = str(scenario.get("id") or "")
    title = str(scenario.get("title") or scenario.get("name") or "")
    if sid in executed:
        return executed[sid]
    title_key = title.casefold()
    if title_key in executed:
        return executed[title_key]
    slug = _slug(title)
    for key, test in executed.items():
        if slug and slug in _slug(key):
            return test
        hay = " ".join(str(test.get(k) or "") for k in ("id", "title", "file", "name")).casefold()
        if sid.casefold() in hay or slug in hay.replace("_", "-"):
            return test
    ext = str(scenario.get("external_id") or "")
    emp = re.search(r"(\d{4,})", ext)
    if emp:
        token = emp.group(1)
        for test in executed.values():
            blob = " ".join(str(test.get(k) or "") for k in ("id", "title", "file", "name"))
            if token in blob:
                return test
    return None


def _scenario_type_counts(
    test_cases: List[Dict[str, Any]], *, executed_only: bool = False
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for case in test_cases:
        if executed_only and not case.get("executed"):
            continue
        if executed_only and case.get("status") == "not_executed":
            continue
        st = case.get("scenario_type", "happy_path")
        counts[st] = counts.get(st, 0) + 1
    return counts


def _build_evidence_groups(
    test_cases: List[Dict[str, Any]],
    assets: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group screenshots/videos under each test case for the Evidence tab."""
    visual_types = {"screenshot", "video", "image"}
    media_assets = [a for a in assets if a.get("media_type") in visual_types]
    assigned: set[str] = set()
    groups: List[Dict[str, Any]] = []

    for case in test_cases:
        if not case.get("executed"):
            continue
        case_id = str(case.get("id") or "")
        case_title = str(case.get("title") or case_id)
        case_slug = _slug(case_title)
        case_evidence_key = evidence_key_from_title(case_title)
        case_cp = str(case.get("cp_code") or "")
        obl_id = str(case.get("obligation_id") or obligation_id_from_title(case_title) or "")
        matched: List[Dict[str, Any]] = list(case.get("evidence") or [])
        for pre in matched:
            assigned.add(str(pre.get("id") or pre.get("relative_path") or ""))

        for asset in media_assets:
            aid = str(asset.get("id") or asset.get("relative_path") or "")
            if aid in assigned:
                continue
            tid = str(asset.get("test_id") or "")
            ttitle = str(asset.get("test_title") or "")
            tid_key = evidence_key_from_title(tid) if tid else ""
            blob = f"{tid} {ttitle}".casefold()
            if (
                tid == case_id
                or tid == case_evidence_key
                or tid_key == case_evidence_key
                or (case_cp and case_cp.casefold() in blob)
                or case_id in tid
                or tid in case_id
                or (obl_id and obl_id.casefold() in blob)
                or (case_slug and case_slug in _slug(blob))
                or (case_title.casefold() in ttitle.casefold())
            ):
                matched.append(asset)
                assigned.add(aid)

        if not matched:
            continue
        screenshots = [a for a in matched if a.get("media_type") in {"screenshot", "image"}]
        videos = [a for a in matched if a.get("media_type") == "video"]
        groups.append(
            {
                "test_id": case_id,
                "test_title": case_title,
                "scenario_type": case.get("scenario_type", "happy_path"),
                "status": case.get("status"),
                "screenshots": screenshots,
                "videos": videos,
            }
        )

    unassigned = [
        a
        for a in media_assets
        if str(a.get("id") or a.get("relative_path") or "") not in assigned
    ]
    if unassigned:
        groups.append(
            {
                "test_id": "general",
                "test_title": "General / unassigned",
                "scenario_type": "happy_path",
                "status": "unknown",
                "screenshots": [a for a in unassigned if a.get("media_type") in {"screenshot", "image"}],
                "videos": [a for a in unassigned if a.get("media_type") == "video"],
            }
        )
    return groups


def _build_test_matrix_summary(
    results: Dict[str, Any],
    test_cases: List[Dict[str, Any]],
    *,
    infra_blocker: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    scenarios = _coerce_list(_coerce_dict(results.get("test_design")).get("functional_scenarios"))
    plan_scenarios = _coerce_list(_coerce_dict(results.get("test_plan")).get("scenarios"))
    generated = _coerce_dict(results.get("generated_code"))
    codegen = _coerce_dict(generated.get("codegen"))
    golden_id = generated.get("golden_template") or codegen.get("golden_template")
    golden_scope = bool(golden_id) and plan_scenarios
    if golden_scope:
        designed = len(plan_scenarios)
        automatable_designed = sum(
            1 for sc in plan_scenarios if isinstance(sc, dict) and not sc.get("playwright_skip")
        )
    else:
        designed = len(scenarios) or len(plan_scenarios)
        automatable_designed = sum(
            1
            for sc in scenarios
            if isinstance(sc, dict)
            and not sc.get("playwright_skip")
            and sc.get("automation_tier") != "manual"
        )
    executed_count = sum(1 for case in test_cases if case.get("executed"))
    blocked_count = sum(1 for case in test_cases if case.get("status") == "blocked")
    failed_count = sum(1 for case in test_cases if case.get("status") == "failed")
    not_executed_count = sum(1 for case in test_cases if case.get("status") == "not_executed")
    design_only_count = sum(
        1
        for case in test_cases
        if not case.get("executed") and case.get("status") in {"skipped", "not_executed"}
    )
    manual_count = sum(1 for case in test_cases if case.get("status") == "manual")
    hidden_by_default = max(0, len(test_cases) - executed_count - blocked_count)

    note_parts = []
    if golden_scope and executed_count >= automatable_designed and automatable_designed:
        note_parts.append(
            f"Alcance golden INC1353166: {executed_count}/{automatable_designed} escenarios automatizados ejecutados."
        )
    elif designed and executed_count < designed:
        note_parts.append(
            f"{designed} escenarios diseñados; {executed_count} ejecutados por Playwright en esta corrida."
        )
    if hidden_by_default:
        note_parts.append(
            f"La matriz incluye {len(test_cases)} filas; la vista por defecto muestra ejecutados/bloqueados "
            f"({executed_count + blocked_count}), ocultando {hidden_by_default} diseñados sin ejecución."
        )
    if infra_blocker:
        note_parts.append(
            "La ejecución falló por infraestructura (navegador/Docker); los fallos funcionales no son válidos."
        )

    return {
        "scenarios_designed": designed,
        "plan_scenarios": len(plan_scenarios),
        "automatable_designed": automatable_designed or designed,
        "scenarios_in_matrix": len(test_cases),
        "scenarios_executed": executed_count,
        "tests_blocked": blocked_count,
        "tests_failed": failed_count,
        "not_executed_count": not_executed_count,
        "design_only_count": design_only_count,
        "manual_count": manual_count,
        "hidden_by_default_filter": hidden_by_default,
        "visibility_note": " ".join(note_parts),
        "infrastructure_blocker": infra_blocker,
        "golden_automation_scope": golden_scope,
        "golden_scenario_count": len(plan_scenarios) if golden_scope else 0,
    }


def build_visual_report(
    run_id: str,
    summary: Dict[str, Any],
    *,
    reports: Optional[Dict[str, Any]] = None,
    evidence_manifest: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compose a UI-ready visual report from pipeline artifacts."""
    results = _coerce_dict(summary.get("results"))
    reports = reports or _coerce_dict(results.get("reports"))
    executive = _coerce_dict(reports.get("executive"))
    automation = _coerce_dict(reports.get("automation"))
    defects_report = _coerce_dict(reports.get("defects"))
    deliverable = _coerce_dict(reports.get("deliverable"))
    rca_report = _coerce_dict(reports.get("rca"))
    healing_report = _coerce_dict(reports.get("healing"))
    qa_report = _coerce_dict(reports.get("qa"))
    rca_payload = _coerce_dict(rca_report.get("rca")) or rca_report

    execution = recover_execution_from_artifacts(
        _coerce_dict(automation.get("execution")),
        run_id=run_id,
        summary=summary,
        reports=reports,
        evidence_manifest=evidence_manifest or {},
    )
    results_execution = _coerce_dict(results.get("execution"))
    if results_execution:
        for key in ("failures", "stderr", "stdout", "error", "compile_errors"):
            if results_execution.get(key) and not execution.get(key):
                execution[key] = results_execution[key]
        if not execution.get("executed_tests") and results_execution.get("executed_tests"):
            execution["executed_tests"] = results_execution["executed_tests"]
    infra_blocker = (
        _coerce_dict(defects_report.get("infrastructure_blocker"))
        or detect_execution_infrastructure_blocker(execution, rca_payload)
    )
    validation_ctx = validation_blocked_context(execution, rca_payload)
    validation_blocked = bool(validation_ctx.get("validation_blocked") or infra_blocker)
    if infra_blocker:
        execution = apply_infrastructure_execution_semantics(execution, infra_blocker)
    defects = _coerce_list(defects_report.get("defects"))
    if infra_blocker:
        defects = consolidate_infrastructure_defects(defects, infra_blocker, execution=execution)
    evidence_manifest = evidence_manifest or {}
    manifest = manifest or {}

    passed = int(execution.get("passed") or evidence_manifest.get("tests_passed") or 0)
    failed = int(execution.get("functional_failed", execution.get("failed")) or 0)
    if infra_blocker:
        failed = 0
    total = int(execution.get("total") or 0) or passed + failed or len(execution.get("executed_tests") or [])

    interpreted = _coerce_dict(qa_report.get("interpreted_intake"))
    interpreted_obligations = _coerce_list(interpreted.get("obligations"))
    deliverable_meta = interpreted.get("deliverable_metadata") or {}
    validation = _coerce_dict(deliverable.get("validation"))

    reports_for_cases = {
        **reports,
        "automation": {**automation, "execution": execution},
    }
    evidence_assets = _build_evidence_assets(run_id, evidence_manifest, reports_for_cases.get("automation", {}))
    test_cases = _build_test_cases(
        run_id,
        results,
        reports_for_cases,
        evidence_assets,
        interpreted_obligations=interpreted_obligations,
    )
    if infra_blocker:
        for case in test_cases:
            if case.get("executed") and case.get("status") == "failed":
                err = sanitize_playwright_error(str(case.get("error") or case.get("actual_result") or ""))
                if not err or is_infrastructure_failure(err):
                    case["status"] = "blocked"
                    case["block_reason"] = "infrastructure"
                    case["actual_result"] = "blocked_infrastructure"
    test_matrix_summary = _build_test_matrix_summary(
        results, test_cases, infra_blocker=infra_blocker or None
    )
    blocked = int(
        execution.get("tests_blocked")
        or test_matrix_summary.get("tests_blocked")
        or (infra_blocker or {}).get("affected_test_count")
        or 0
    )
    automation_breakdown = _automation_breakdown(interpreted_obligations)

    release_readiness = executive.get("release_readiness_score")
    if validation_blocked and executive.get("design_readiness_score") is not None:
        release_readiness = executive.get("design_readiness_score")
    release_decision = (
        "invalid_environment"
        if infra_blocker
        else (
            defects_report.get("release_readiness")
            or executive.get("release_readiness")
            or ("go" if (release_readiness or 0) >= 70 and blocked == 0 else "go_with_risk")
        )
    )

    site_exploration = _coerce_dict(results.get("site_exploration"))

    pass_rate = execution.get("pass_rate")
    if (not pass_rate and total) or execution.get("recovered_from_disk") or execution.get("recovered_from_manifest"):
        pass_rate = passed / total if total else 0.0
    else:
        pass_rate = pass_rate or 0

    return {
        "type": "visual",
        "run_id": run_id,
        "project_id": summary.get("project_id"),
        "status": summary.get("status"),
        "duration_seconds": summary.get("duration_seconds"),
        "executive": {
            "release_readiness_score": release_readiness,
            "design_readiness_score": executive.get("design_readiness_score"),
            "validation_blocked": validation_blocked,
            "environment_status": executive.get("environment_status"),
            "product_readiness_verdict": executive.get("product_readiness_verdict"),
            "release_decision": release_decision,
            "executive_summary": executive.get("executive_summary"),
            "technical_summary": executive.get("technical_summary"),
            "product_summary": executive.get("product_summary"),
            "recommendations": coerce_string_list(executive.get("recommendations")),
            "tests_total": total,
            "tests_passed": passed,
            "tests_failed": failed,
            "tests_blocked": blocked,
            "pass_rate": pass_rate,
            "defects_total": len(defects),
            "critical_defects": [
                d for d in defects if str(d.get("severity", "")).lower() in {"critical", "high", "blocker"}
            ][:10],
            "scenario_coverage": _scenario_type_counts(test_cases, executed_only=True),
            "design_scenario_coverage": _scenario_type_counts(test_cases, executed_only=False),
            "automation_breakdown": automation_breakdown,
            "manual_obligations": automation_breakdown.get("manual", 0),
            "automatable_obligations": automation_breakdown.get("automated", 0)
            + automation_breakdown.get("partial", 0),
            "infrastructure_blocker": infra_blocker or None,
            "release_verdict": "invalid" if infra_blocker else "functional",
            "test_matrix_summary": test_matrix_summary,
        },
        "technical": {
            "execution_status": execution.get("status"),
            "execution_mode": execution.get("mode"),
            "duration_seconds": execution.get("duration_seconds") or summary.get("duration_seconds"),
            "compile_errors": execution.get("compile_errors", 0),
            "infrastructure_blocker": infra_blocker or None,
            "release_verdict": "invalid" if infra_blocker else "functional",
            "heal_attempts": automation.get("heal_attempts", 0),
            "healing_success_pct": healing_report.get("healing_success_pct"),
            "playwright_project": (automation.get("generated_code") or {}).get("project_path"),
            "generated_file_count": (automation.get("generated_code") or {}).get("file_count", 0),
            "generated_files": (automation.get("generated_code") or {}).get("files") or [],
            "rca_categories": _rca_categories(_coerce_dict(rca_report.get("rca"))),
            "defect_severity": _severity_counts(defects),
            "site_exploration": {
                "routes": site_exploration.get("routes") or [],
                "negative_opportunities": site_exploration.get("negative_opportunities") or [],
                "explored": bool(site_exploration),
            },
        },
        "test_cases": test_cases,
        "evidence": {
            "screenshot_count": evidence_manifest.get("screenshot_count", 0),
            "video_count": evidence_manifest.get("video_count", 0),
            "diagnostic_count": evidence_manifest.get("diagnostic_count", 0),
            "assets": evidence_assets,
            "by_test": _group_evidence_by_test(evidence_assets),
            "groups": _build_evidence_groups(test_cases, evidence_assets),
        },
        "deliverable": {
            "inc": deliverable_meta.get("inc_id") or deliverable_meta.get("inc"),
            "tec": deliverable_meta.get("tec"),
            "path": deliverable.get("path"),
            "validation": validation,
            "row_count": deliverable.get("obligation_rows") and len(deliverable.get("obligation_rows") or []),
            "download_url": f"/api/v1/runs/{run_id}/artifacts/deliverable.xlsx",
        },
        "artifacts": {
            "manifest_url": f"/api/v1/runs/{run_id}/artifacts/manifest",
            "bundles": manifest.get("bundles")
            or {
                "full": f"/api/v1/runs/{run_id}/artifacts?bundle=full",
                "scripts": f"/api/v1/runs/{run_id}/artifacts?bundle=scripts",
                "evidence": f"/api/v1/runs/{run_id}/artifacts?bundle=evidence",
                "deliverable": f"/api/v1/runs/{run_id}/artifacts/deliverable.xlsx",
            },
            "inventory": manifest.get("inventory") or {},
        },
    }


def _group_evidence_by_test(assets: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for asset in assets:
        key = str(asset.get("test_id") or "general")
        grouped.setdefault(key, []).append(asset)
    return grouped


def load_evidence_manifest_from_disk(run_id: str) -> Dict[str, Any]:
    from src.services.artifact_bundle import find_artifact_dir_on_disk

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if not artifact_dir:
        return {}
    manifest_path = artifact_dir / "evidence" / "evidence-manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def resolve_evidence_file(run_id: str, relative_path: str) -> Optional[Path]:
    """Resolve an evidence file path safely within the run artifact directory."""
    from src.services.artifact_bundle import find_artifact_dir_on_disk

    rel = Path(relative_path or "").as_posix().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if not artifact_dir:
        return None

    candidates = [
        artifact_dir / rel,
        artifact_dir / "evidence" / rel,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            root = artifact_dir.resolve()
            if resolved.is_file() and str(resolved).startswith(str(root)):
                return resolved
        except OSError:
            continue
    return None
