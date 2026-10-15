"""Cooperative cancellation for in-flight pipeline runs."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.memory.enhanced_memory import MemoryTier, get_memory
from src.services.run_tracker import append_run_event


class RunCancelledError(Exception):
    """Raised when a pipeline run was cancelled by the operator."""


def _cancel_key(run_id: str) -> str:
    return f"run_cancel:{run_id}"


def is_cancelled(run_id: str) -> bool:
    if not run_id:
        return False
    return bool(get_memory().read(_cancel_key(run_id)))


def clear_cancel(run_id: str) -> None:
    if run_id:
        get_memory().delete(_cancel_key(run_id))


def ensure_not_cancelled(run_id: str) -> None:
    if is_cancelled(run_id):
        raise RunCancelledError(f"Run {run_id} was cancelled by the operator")


def request_cancel(run_id: str, *, get_status) -> Dict[str, Any]:
    """
    Mark a run for cancellation. Returns payload for API response.
    Raises ValueError when the run cannot be cancelled.
    """
    status = get_status(run_id)
    if not status:
        raise ValueError("Run not found")

    current = status.get("status")
    if current in {"completed", "failed", "partial"}:
        raise ValueError(f"Run is already {current}")

    if current == "cancelled" or is_cancelled(run_id):
        return {
            "run_id": run_id,
            "status": "cancelled",
            "cancelled_at_stage": status.get("cancelled_at_stage") or status.get("stage"),
            "already_requested": True,
        }

    memory = get_memory()
    memory.write(_cancel_key(run_id), True, tier=MemoryTier.SHORT_TERM)

    stage = status.get("stage") or "running"
    updated = {
        **status,
        "status": "cancelled",
        "stage": "cancelled",
        "stage_label": "Cancelled",
        "cancelled_at_stage": stage,
    }
    memory.write(f"pipeline_status:{run_id}", updated, tier=MemoryTier.SHORT_TERM)

    events = memory.read(f"run_events:{run_id}") or []
    already_logged = any(e.get("type") == "run_cancelled" for e in events)
    if not already_logged:
        append_run_event(
            run_id,
            "run_cancelled",
            "Pipeline cancelled by operator",
            stage="cancelled",
            level="warn",
            details={"cancelled_at_stage": stage},
        )

    return {
        "run_id": run_id,
        "status": "cancelled",
        "cancelled_at_stage": stage,
        "already_requested": current == "cancelled",
    }
