"""Operator-provided runtime hints (e.g. target URL) injected before or during intake."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.memory.enhanced_memory import MemoryTier, get_memory


def _key(run_id: str) -> str:
    return f"run_hints:{run_id}"


def set_run_hints(run_id: str, hints: Dict[str, Any]) -> Dict[str, Any]:
    memory = get_memory()
    current = memory.read(_key(run_id)) or {}
    merged = {**current, **{k: v for k, v in hints.items() if v not in (None, "")}}
    memory.write(_key(run_id), merged, tier=MemoryTier.SHORT_TERM)
    return merged


def get_run_hints(run_id: str) -> Dict[str, Any]:
    return get_memory().read(_key(run_id)) or {}


def merge_hints_into_metadata(metadata: Optional[Dict[str, Any]], run_id: str) -> Dict[str, Any]:
    meta = dict(metadata or {})
    hints = get_run_hints(run_id)
    if hints.get("target_url") and not meta.get("target_url"):
        meta["target_url"] = hints["target_url"]
    notes = list(meta.get("operator_notes") or [])
    if hints.get("operator_note"):
        notes.append(str(hints["operator_note"]))
    if hints.get("target_url") and not any("target url" in n.lower() for n in notes):
        notes.append(f"Operator provided target URL: {hints['target_url']}")
    if notes:
        meta["operator_notes"] = notes
    return meta
