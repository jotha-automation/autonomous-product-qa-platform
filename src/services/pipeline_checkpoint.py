"""Persist paused or failed pipeline state until operator resumes."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from src.memory.enhanced_memory import MemoryTier, get_memory


def _key(run_id: str) -> str:
    return f"pipeline_checkpoint:{run_id}"


def save_checkpoint(run_id: str, data: Dict[str, Any]) -> None:
    get_memory().write(_key(run_id), data, tier=MemoryTier.LONG_TERM)


def load_checkpoint(run_id: str) -> Optional[Dict[str, Any]]:
    return get_memory().read(_key(run_id))


def clear_checkpoint(run_id: str) -> None:
    get_memory().delete(_key(run_id))


def _load_disk_pipeline_summary(run_id: str) -> Optional[Dict[str, Any]]:
    from src.services.artifact_bundle import find_artifact_dir_on_disk

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if not artifact_dir:
        return None
    summary_path = artifact_dir / "pipeline_summary.json"
    if not summary_path.is_file():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _pipeline_summary_for_run(run_id: str) -> Optional[Dict[str, Any]]:
    summary = get_memory().read(f"pipeline_summary:{run_id}")
    if summary:
        return summary
    return _load_disk_pipeline_summary(run_id)


def _failed_stage_from_summary(summary: Dict[str, Any]) -> str:
    explicit = summary.get("failed_at_stage") or summary.get("resume_from")
    if explicit and explicit not in {"failed", ""}:
        return str(explicit)
    stage = summary.get("stage") or ""
    if stage and stage not in {"failed", ""}:
        return str(stage)
    return "playwright_architect"


def is_awaiting_target_url(run_id: str) -> bool:
    cp = load_checkpoint(run_id)
    return bool(cp and cp.get("awaiting") == "target_url")


def is_resumable(run_id: str) -> bool:
    cp = load_checkpoint(run_id)
    if cp and cp.get("awaiting") in {"resume", "target_url"}:
        return True
    summary = _pipeline_summary_for_run(run_id)
    return bool(summary and summary.get("status") == "failed")


def save_failure_checkpoint(
    run_id: str,
    *,
    failed_stage: str,
    project_id: str,
    partial_results: Dict[str, Any],
    metadata: Dict[str, Any],
    start_time: str,
    mode: str = "full",
) -> None:
    save_checkpoint(
        run_id,
        {
            "awaiting": "resume",
            "failed_stage": failed_stage,
            "run_id": run_id,
            "project_id": project_id,
            "mode": mode,
            "metadata": metadata,
            "partial_results": partial_results,
            "start_time": start_time,
        },
    )


def load_resume_checkpoint(run_id: str) -> Optional[Dict[str, Any]]:
    """Return a resume checkpoint from explicit storage or a failed run summary."""
    cp = load_checkpoint(run_id)
    if cp and cp.get("awaiting") == "resume":
        return cp
    if cp and cp.get("awaiting") == "target_url":
        return {
            "awaiting": "resume",
            "failed_stage": "intake_interpretation",
            "run_id": run_id,
            "project_id": cp.get("project_id", "demo"),
            "mode": cp.get("mode", "full"),
            "metadata": dict(cp.get("metadata") or {}),
            "partial_results": dict(cp.get("partial_results") or {}),
            "start_time": cp.get("start_time"),
        }
    summary = _pipeline_summary_for_run(run_id)
    if not summary or summary.get("status") != "failed":
        return None
    return {
        "awaiting": "resume",
        "failed_stage": _failed_stage_from_summary(summary),
        "run_id": run_id,
        "project_id": summary.get("project_id", "demo"),
        "mode": summary.get("mode", "full"),
        "metadata": summary.get("metadata") or {},
        "partial_results": summary.get("results") or {},
        "start_time": summary.get("start_time"),
    }
