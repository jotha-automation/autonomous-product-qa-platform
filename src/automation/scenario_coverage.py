"""Build Playwright scenarios with one entry per intake obligation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.automation.obligation_steps import (
    automatable_obligations,
    map_manual_obligation_scenario,
    map_obligation_to_scenario,
)


def _generic_obligation_scenario(obl: Dict[str, Any], target_url: str) -> Dict[str, Any]:
    obl_id = obl.get("id") or "OBL-001"
    title = (obl.get("title") or obl_id)[:200]
    steps = []
    for i, raw in enumerate((obl.get("steps") or [])[:12]):
        steps.append(
            {
                "id": f"step-{i + 1:02d}",
                "action": "note",
                "target": str(raw)[:500],
                "data": "",
                "capture_evidence": i == 0,
            }
        )
    if not steps:
        steps = [
            {
                "id": "step-01-goto",
                "action": "goto",
                "target": "path",
                "data": "/",
                "capture_evidence": True,
            }
        ]
    return {
        "id": obl_id,
        "name": title,
        "priority": "P1",
        "page_object": "BasePage",
        "requirement_ids": [obl_id],
        "preconditions": obl.get("preconditions") or [],
        "evidence_steps": [s["id"] for s in steps if s.get("capture_evidence")],
        "steps": steps,
        "assertions": [{"type": "visible", "target": "body", "expected": "true"}],
        "locators": {"primary": "body", "fallback": "html"},
        "tags": ["regression", "intake-obligation"],
        "automation_tier": obl.get("automation_tier", "partial"),
        "target_url": target_url,
    }


def build_scenarios_from_obligations(
    obligations: List[Dict[str, Any]],
    target_url: str,
    *,
    bank_portal: bool = False,
    include_manual: bool = True,
) -> List[Dict[str, Any]]:
    """One scenario per obligation — manual cases are tagged for test.skip in codegen."""
    scenarios: List[Dict[str, Any]] = []
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        tier = obl.get("automation_tier", "partial")
        if tier == "manual":
            if not include_manual:
                continue
            scenarios.append(map_manual_obligation_scenario(obl, target_url))
        elif bank_portal:
            scenarios.append(map_obligation_to_scenario(obl, target_url))
        else:
            scenarios.append(_generic_obligation_scenario(obl, target_url))
    return scenarios


def merge_scenario_coverage(
    baseline: List[Dict[str, Any]],
    refined: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep every baseline obligation scenario; overlay LLM refinements by id."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for sc in refined:
        oid = str(sc.get("id") or "")
        if oid:
            by_id[oid] = sc

    merged: List[Dict[str, Any]] = []
    for base in baseline:
        oid = str(base.get("id") or "")
        patch = by_id.get(oid)
        if patch:
            merged.append(
                {
                    **base,
                    **patch,
                    "id": oid or patch.get("id"),
                    "steps": patch.get("steps") or base.get("steps"),
                    "assertions": patch.get("assertions") or base.get("assertions"),
                    "manual": base.get("manual") or patch.get("manual"),
                    "automation_tier": base.get("automation_tier") or patch.get("automation_tier"),
                }
            )
        else:
            merged.append(base)

    seen = {str(s.get("id")) for s in merged if s.get("id")}
    for sc in refined:
        oid = str(sc.get("id") or "")
        if oid and oid not in seen:
            merged.append(sc)
            seen.add(oid)
    return merged


def automatable_scenario_count(obligations: List[Dict[str, Any]]) -> int:
    return len(automatable_obligations(obligations))
