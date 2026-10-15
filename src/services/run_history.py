"""Merge pipeline run history from DB, local disk, and Drive."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.services.artifact_bundle import GENERATED_PROJECTS_ROOT

_PUBLISHED_REPORT_KEYS = frozenset(
    {"qa", "automation", "executive", "rca", "healing", "defects", "deliverable", "visual"}
)
_INTERNAL_ARTIFACT_NAMES = frozenset({"pipeline_summary.json", "manifest.json"})


def has_published_reports_file(reports_path: Path) -> bool:
    """True when reports.json contains a built report bundle, not an empty stub."""
    if not reports_path.is_file():
        return False
    try:
        data = json.loads(reports_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return any(key in _PUBLISHED_REPORT_KEYS for key in data)


def count_publishable_artifact_files(artifact_dir: Path) -> int:
    """Count deliverable files under a run artifact dir (exclude internal metadata JSON)."""
    if not artifact_dir.is_dir():
        return 0
    return sum(
        1
        for path in artifact_dir.rglob("*")
        if path.is_file() and path.name not in _INTERNAL_ARTIFACT_NAMES
    )


def is_internal_artifact_name(name: str) -> bool:
    return name in _INTERNAL_ARTIFACT_NAMES


def is_unscheduled_probe_run(summary: Dict[str, Any]) -> bool:
    """Watcher-triggered run that paused before the operator started QA."""
    return (
        summary.get("status") == "awaiting_input"
        and summary.get("stage") == "awaiting_target_url"
    )


def run_looks_like_unscheduled_probe(run: Dict[str, Any]) -> bool:
    artifact_dir = run.get("artifact_dir")
    if artifact_dir:
        summary_path = Path(artifact_dir) / "pipeline_summary.json"
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return False
            return is_unscheduled_probe_run(summary)
    return run.get("status") == "awaiting_input" and not run.get("has_reports")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _summary_timestamps(summary_path: Path) -> Dict[str, Optional[datetime]]:
    if not summary_path.is_file():
        return {}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        "started_at": _parse_timestamp(data.get("start_time")),
        "completed_at": _parse_timestamp(data.get("end_time")),
    }


def _run_sort_timestamp(run: Dict[str, Any]) -> datetime:
    for key in ("completed_at", "started_at", "collected_at"):
        parsed = _parse_timestamp(run.get(key))
        if parsed:
            return parsed

    artifact_dir = run.get("artifact_dir")
    if artifact_dir:
        summary_path = Path(artifact_dir) / "pipeline_summary.json"
        stamps = _summary_timestamps(summary_path)
        for key in ("completed_at", "started_at"):
            if stamps.get(key):
                return stamps[key]  # type: ignore[return-value]

        manifest_path = Path(artifact_dir) / "evidence" / "evidence-manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                parsed = _parse_timestamp(manifest.get("collected_at"))
                if parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

        try:
            return datetime.fromtimestamp(Path(artifact_dir).stat().st_mtime, tz=timezone.utc)
        except OSError:
            pass

    return datetime.min.replace(tzinfo=timezone.utc)


def list_local_artifact_runs(project_id: str) -> List[Dict[str, Any]]:
    """Discover runs under generated-test-projects/{project_id}/aptp-artifacts/."""
    artifact_root = GENERATED_PROJECTS_ROOT / project_id / "aptp-artifacts"
    if not artifact_root.is_dir():
        return []

    runs: List[Dict[str, Any]] = []
    for run_dir in artifact_root.iterdir():
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        summary_path = run_dir / "pipeline_summary.json"
        status = "unknown"
        started_at = None
        completed_at = None
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                status = summary.get("status", status)
                stamps = _summary_timestamps(summary_path)
                started_at = stamps.get("started_at")
                completed_at = stamps.get("completed_at")
                if started_at:
                    started_at = started_at.isoformat()
                if completed_at:
                    completed_at = completed_at.isoformat()
            except json.JSONDecodeError:
                pass
        file_count = count_publishable_artifact_files(run_dir)
        runs.append(
            {
                "run_id": run_id,
                "status": status,
                "source": "local",
                "artifact_dir": str(run_dir),
                "file_count": file_count,
                "has_reports": has_published_reports_file(run_dir / "reports.json"),
                "has_scripts": (run_dir / "scripts").is_dir(),
                "has_evidence": (run_dir / "evidence").is_dir() or (run_dir / "execution-report.pdf").is_file(),
                "started_at": started_at,
                "completed_at": completed_at,
                "manifest_file_id": None,
                "files": [],
            }
        )
    return runs


def merge_run_histories(*sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge run lists by run_id; later sources enrich earlier ones."""
    merged: Dict[str, Dict[str, Any]] = {}
    for source_runs in sources:
        for run in source_runs:
            run_id = run.get("run_id")
            if not run_id:
                continue
            if run_id not in merged:
                merged[run_id] = dict(run)
                continue
            existing = merged[run_id]
            for key, value in run.items():
                if value in (None, "", [], 0, False):
                    continue
                if key == "files" and value:
                    existing["files"] = value
                elif key == "source" and existing.get("source") != value:
                    if existing.get("source") and value:
                        existing["source"] = "local+drive" if "drive" in (existing["source"], value) else value
                    else:
                        existing["source"] = value
                else:
                    existing[key] = value
    return sorted(merged.values(), key=_run_sort_timestamp, reverse=True)


def load_local_run_reports(run_id: str) -> Optional[Dict[str, Any]]:
    """Load reports + summary from local aptp-artifacts when Drive is unavailable."""
    matches = sorted(GENERATED_PROJECTS_ROOT.glob(f"*/aptp-artifacts/{run_id}"))
    for run_dir in matches:
        reports_path = run_dir / "reports.json"
        summary_path = run_dir / "pipeline_summary.json"
        if not reports_path.is_file():
            continue
        try:
            reports = json.loads(reports_path.read_text(encoding="utf-8"))
            summary = (
                json.loads(summary_path.read_text(encoding="utf-8"))
                if summary_path.is_file()
                else {}
            )
        except json.JSONDecodeError:
            continue
        project_id = summary.get("project_id") or run_dir.parent.parent.name
        return {
            "drive_folder_id": None,
            "project_id": project_id,
            "run_id": run_id,
            "run_folder_id": None,
            "source": "local",
            "artifact_dir": str(run_dir),
            "reports": reports,
            "pipeline_summary": summary,
        }
    return None
