"""Deduplicate intake obligations for automation and reporting."""
from __future__ import annotations

import re
from typing import Any, Dict, List


def _external_key(obligation: Dict[str, Any]) -> str:
    ext = str(obligation.get("external_id") or "")
    match = re.search(r"(\d{4,})", ext)
    if match:
        return f"emp-{match.group(1)}"
    obl_id = str(obligation.get("id") or "")
    match = re.search(r"(\d{4,})", obl_id)
    if match:
        return f"obl-{match.group(1)}"
    title = str(obligation.get("title") or "").casefold()
    return title[:120] or obl_id


def dedupe_obligations(obligations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep one obligation per external EMPRESARIAL id (prefer automated tier)."""
    ranked: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    tier_rank = {"automated": 0, "partial": 1, "manual": 2}

    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        key = _external_key(obl)
        if key not in ranked:
            ranked[key] = obl
            order.append(key)
            continue
        current = ranked[key]
        cur_tier = tier_rank.get(str(current.get("automation_tier") or "partial"), 1)
        new_tier = tier_rank.get(str(obl.get("automation_tier") or "partial"), 1)
        if new_tier < cur_tier:
            ranked[key] = obl

    return [ranked[k] for k in order if k in ranked]
