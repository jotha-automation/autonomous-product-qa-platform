"""Defensive numeric coercion for LLM-originated report fields."""
from __future__ import annotations

from typing import Any


def coerce_score_float(value: Any, default: float = 0.5) -> float:
    """Normalize scores that may arrive as int, str, list, or nested dict from agents."""
    if value is None:
        return default
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().rstrip("%")
        if not stripped:
            return default
        try:
            return float(stripped)
        except ValueError:
            return default
    if isinstance(value, list):
        parsed = [coerce_score_float(item, default) for item in value if item is not None]
        if not parsed:
            return default
        return sum(parsed) / len(parsed)
    if isinstance(value, dict):
        for key in ("score", "value", "functional", "release_risk_score", "coverage_score"):
            if key in value:
                return coerce_score_float(value[key], default)
    return default
