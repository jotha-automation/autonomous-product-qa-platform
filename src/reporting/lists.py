"""Normalize list-shaped report fields from LLM or legacy payloads."""
from __future__ import annotations

from typing import Any, List


def coerce_string_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []
