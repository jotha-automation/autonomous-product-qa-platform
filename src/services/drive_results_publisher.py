"""Publish pipeline artifacts to Google Drive under QA Platform Results/."""
import json
import logging
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Tuple

from src.connectors.google_drive.connector import get_google_drive_connector
from src.connectors.google_drive.errors import is_storage_quota_error, message_from_drive_error
from src.services.artifact_bundle import _project_path_from_summary, write_run_reports_to_disk
from googleapiclient.errors import HttpError


def sanitize_relative_path(path: str) -> str:
    import re

    parts = []
    for part in PurePosixPath(path.replace("\\", "/")).parts:
        if part in ("", ".", ".."):
            continue
        if part.startswith("."):
            continue
        parts.append(re.sub(r"[^\w\-\. ]+", "_", part))
    return "/".join(parts)


async def _ensure_path(connector, root_folder_id: str, relative_dir: str) -> str:
    if not relative_dir or relative_dir == ".":
        return root_folder_id
    current = root_folder_id
    for segment in PurePosixPath(relative_dir).parts:
        current = await connector.create_folder(current, segment)
    return current

logger = logging.getLogger("drive_results_publisher")

from src.services.project_limits import QA_RESULTS_FOLDER

SKIP_UPLOAD_PARTS = {"node_modules", ".git", "__pycache__", "aptp-artifacts"}
_LEGACY_QA_RESULTS_NAMES = {"qa platform results", "qaplatformresults"}


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def _qa_results_folder_name(name: str) -> bool:
    key = (name or "").strip().casefold().replace("  ", " ")
    return key in _LEGACY_QA_RESULTS_NAMES or key == QA_RESULTS_FOLDER.casefold()


def collect_local_artifact_files(run_id: str, summary: Dict[str, Any]) -> List[Tuple[str, bytes, str]]:
    """Files to upload (relative path within run folder, content, mime)."""
    files: List[Tuple[str, bytes, str]] = []
    project_dir = _project_path_from_summary(summary)

    if project_dir:
        write_run_reports_to_disk(run_id, summary)

    reports = (summary.get("results") or {}).get("reports") or {}
    if reports:
        files.append(("reports.json", json.dumps(reports, indent=2, ensure_ascii=False).encode("utf-8"), "application/json"))
    files.append(
        (
            "pipeline_summary.json",
            json.dumps(summary, indent=2, default=str, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )
    )

    if not project_dir:
        manifest = {
            "run_id": run_id,
            "project_id": summary.get("project_id"),
            "status": summary.get("status"),
            "uploaded_files": len(files),
            "note": "playwright_project_not_on_disk",
        }
        files.append(("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"), "application/json"))
        return files

    artifact_dir = project_dir / "aptp-artifacts" / run_id
    if artifact_dir.is_dir():
        for path in artifact_dir.rglob("*"):
            if path.is_file():
                rel = path.relative_to(artifact_dir).as_posix()
                files.append((rel, path.read_bytes(), _guess_mime(path)))

    for sub in ("tests", "pages", "reports", "fixtures"):
        subdir = project_dir / sub
        if subdir.is_dir():
            for path in subdir.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(project_dir).as_posix()
                    files.append((rel, path.read_bytes(), _guess_mime(path)))

    for name in ("package.json", "playwright.config.ts", ".env", "README.md"):
        path = project_dir / name
        if path.is_file():
            files.append((name, path.read_bytes(), _guess_mime(path)))

    manifest = {
        "run_id": run_id,
        "project_id": summary.get("project_id"),
        "status": summary.get("status"),
        "uploaded_files": len(files),
    }
    files.append(("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"), "application/json"))
    return files


async def find_qa_results_root(connector, project_drive_folder_id: str) -> str | None:
    for child in await connector.list_child_folders(project_drive_folder_id):
        if _qa_results_folder_name(child.get("name", "")):
            return child["id"]
    return None


async def publish_pipeline_results_to_drive(
    project_drive_folder_id: str,
    run_id: str,
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    """Upload unpacked pipeline deliverables to {project}/QA Platform Results/{run_id}/."""
    connector = get_google_drive_connector()
    if not connector.is_configured:
        logger.warning("Drive not configured — skipping results upload")
        return {"skipped": True, "reason": "drive_not_configured"}

    local_files = collect_local_artifact_files(run_id, summary)
    if not local_files:
        return {"skipped": True, "reason": "no_local_artifacts"}

    results_root_id = await find_qa_results_root(connector, project_drive_folder_id)
    if not results_root_id:
        results_root_id = await connector.create_folder(project_drive_folder_id, QA_RESULTS_FOLDER)

    run_folder_id = await connector.create_folder(results_root_id, run_id)
    uploaded = []

    try:
        for rel_path, content, mime_type in local_files:
            rel = sanitize_relative_path(rel_path)
            if not rel:
                continue
            path = PurePosixPath(rel)
            parent_id = await _ensure_path(connector, run_folder_id, path.parent.as_posix())
            file_id = await connector.upload_file(parent_id, path.name, content, mime_type)
            uploaded.append({"relative_path": rel, "file_id": file_id})
    except HttpError as exc:
        if is_storage_quota_error(exc):
            msg = message_from_drive_error(exc)
            logger.warning("Drive results upload skipped (no service-account quota): %s", msg)
            return {
                "skipped": True,
                "reason": "storage_quota_exceeded",
                "message": msg,
                "run_folder_id": run_folder_id,
            }
        raise

    logger.info(
        "Uploaded %d files to Drive %s/%s/%s",
        len(uploaded),
        QA_RESULTS_FOLDER,
        run_id,
        project_drive_folder_id[:8],
    )
    return {
        "qa_results_folder": QA_RESULTS_FOLDER,
        "run_folder_id": run_folder_id,
        "run_id": run_id,
        "files_uploaded": len(uploaded),
        "uploaded": uploaded,
    }
