"""Convenciones de nomenclatura APTP: CP_* y TestSet:*."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_TIER_RANK = {"automated": 0, "partial": 1, "manual": 2}

_DEFAULT_TEST_SET_TYPE = "FUNCIONAL"


def format_case_code(index: int, title: str) -> str:
    """Formato entregable: CP_0001_NOMBRE-CASO-PRUEBA."""
    raw = re.sub(r"^(EMPRESARIAL[-\s]?\d+\s*:\s*)", "", title or "", flags=re.I).strip()
    raw = re.sub(r"^OBL-EMP-\d+\s*:\s*", "", raw, flags=re.I).strip()
    slug = re.sub(r"[^\w\s]+", " ", raw, flags=re.UNICODE)
    slug = re.sub(r"\s+", "_", slug.strip().upper())
    slug = slug[:72] or "CASO-PRUEBA"
    return f"CP_{index:04d}_{slug}"


def _normalize_test_set_type(test_type: str) -> str:
    tipo = re.sub(r"[^\w]+", "_", (test_type or _DEFAULT_TEST_SET_TYPE).strip().upper())
    return tipo.strip("_") or _DEFAULT_TEST_SET_TYPE


def _normalize_project_id(project_id: str) -> str:
    pid = re.sub(r"[^\w\-]+", "-", (project_id or "APTP").strip())
    return pid.strip("-") or "APTP"


def format_test_set_name(test_type: str, project_id: str) -> str:
    """Formato suite: TestSet:FUNCIONAL:ID_PROYECTO."""
    return f"TestSet:{_normalize_test_set_type(test_type)}:{_normalize_project_id(project_id)}"


def infer_test_set_type(
    metadata: Optional[Dict[str, Any]] = None,
    *,
    obligations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Inferir tipo de set (FUNCIONAL, REGRESION, etc.) desde metadatos del intake."""
    metadata = metadata or {}
    explicit = metadata.get("test_set_type") or metadata.get("suite_type")
    if explicit:
        return _normalize_test_set_type(str(explicit))

    section = str(metadata.get("suite_section") or "").casefold()
    objetivo = str(metadata.get("objetivo") or metadata.get("objective") or "").casefold()
    blob = f"{section} {objetivo}"
    if "regres" in blob:
        return "REGRESION"
    if "smoke" in blob or "humo" in blob:
        return "SMOKE"
    if obligations:
        manual = sum(1 for o in obligations if isinstance(o, dict) and o.get("automation_tier") == "manual")
        if manual and manual == len(obligations):
            return "MANUAL"
    return _DEFAULT_TEST_SET_TYPE


def assign_case_codes(obligations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Añade cp_code estable por orden de obligación."""
    out: List[Dict[str, Any]] = []
    index = 0
    for ob in obligations:
        if not isinstance(ob, dict):
            continue
        index += 1
        row = dict(ob)
        row["cp_code"] = format_case_code(index, str(ob.get("title") or ob.get("id") or ""))
        out.append(row)
    return out


def merge_automation_tier(*tiers: str) -> str:
    """Conservador: manual > partial > automated."""
    valid = [t for t in tiers if t in _TIER_RANK]
    if not valid:
        return "partial"
    return max(valid, key=lambda t: _TIER_RANK[t])


def automation_tier_label_es(tier: str) -> str:
    labels = {
        "automated": "Automatizable",
        "partial": "Parcialmente automatizable",
        "manual": "Operación manual",
    }
    return labels.get(tier, tier)
