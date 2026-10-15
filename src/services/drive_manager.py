"""Drive project folder management under APTP system root."""
import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from src.connectors.google_drive.connector import get_google_drive_connector
from src.connectors.google_drive.errors import is_storage_quota_error, message_from_drive_error
from src.connectors.google_drive.intake import fetch_documents_from_folder
from src.connectors.google_drive.system_root import (
    get_system_root_folder_id,
    get_system_root_name,
    list_project_folders_under_system_root,
    project_id_from_folder_name,
    sync_system_root,
)
from src.orchestrator.engine import ProductQAOrchestrator
from src.services.drive_results_publisher import find_qa_results_root, publish_pipeline_results_to_drive
from src.services.project_limits import QA_RESULTS_FOLDER
from src.services.intake_registry import get_intake_registry
from src.services.run_history import list_local_artifact_runs, load_local_run_reports, merge_run_histories, run_looks_like_unscheduled_probe
from src.services.run_tracker import append_run_event

logger = logging.getLogger("drive_manager")

_DEFAULT_MODE = os.getenv("APTP_DEFAULT_PIPELINE_MODE", "full")


def _pipeline_has_deliverables(status: str) -> bool:
    """Runs that produced reports/artifacts (success or test failures only)."""
    return status in ("completed", "partial")


async def _publish_run_results_to_drive(
    drive_folder_id: str,
    run_id: str,
    result: Dict[str, Any],
    folder_name: str,
) -> Optional[str]:
    """Upload artifacts to Drive; return run folder id when published."""
    results_folder_id: Optional[str] = None
    try:
        pub = await publish_pipeline_results_to_drive(drive_folder_id, run_id, result)
        if pub.get("skipped"):
            if pub.get("reason") == "storage_quota_exceeded":
                append_run_event(
                    run_id,
                    "drive_results_upload_skipped",
                    pub.get("message") or "Drive upload skipped: service account has no My Drive quota.",
                    stage="completed",
                    level="warn",
                )
            results_folder_id = pub.get("run_folder_id") or results_folder_id
        else:
            results_folder_id = pub.get("run_folder_id")
            append_run_event(
                run_id,
                "drive_results_uploaded",
                f"Published {pub.get('files_uploaded', 0)} files to {QA_RESULTS_FOLDER}",
                stage="completed",
            )
    except Exception as upload_exc:
        if is_storage_quota_error(upload_exc):
            logger.warning(
                "Drive results upload skipped for %s: %s",
                folder_name,
                message_from_drive_error(upload_exc),
            )
            append_run_event(
                run_id,
                "drive_results_upload_skipped",
                message_from_drive_error(upload_exc),
                stage="completed",
                level="warn",
            )
        else:
            logger.exception("Failed to publish QA results to Drive for %s", folder_name)
            append_run_event(
                run_id,
                "drive_results_upload_failed",
                message_from_drive_error(upload_exc),
                stage="completed",
                level="error",
            )
    return results_folder_id


_FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\. ]{1,120}$")


def sanitize_folder_name(name: str) -> str:
    name = name.strip()
    if not name.upper().startswith("APTP-"):
        name = f"APTP-{name}"
    name = re.sub(r"[^\w\-\. ]+", "-", name).strip("-")
    if not _FOLDER_NAME_RE.match(name):
        raise ValueError(f"Invalid folder name: {name}")
    return name


def sanitize_relative_path(path: str) -> str:
    parts = []
    for part in PurePosixPath(path.replace("\\", "/")).parts:
        if part in ("", ".", ".."):
            continue
        if part.startswith("."):
            continue
        parts.append(re.sub(r"[^\w\-\. ]+", "_", part))
    return "/".join(parts)


async def list_projects() -> List[Dict[str, Any]]:
    projects = await list_project_folders_under_system_root()
    registry = get_intake_registry()
    registry_by_drive = {
        r["drive_folder_id"]: r
        for r in await registry.list_project_folders(get_system_root_folder_id())
    }
    out: List[Dict[str, Any]] = []
    for p in projects:
        reg = registry_by_drive.get(p.drive_folder_id)
        processing = (
            {k: reg[k] for k in (
                "processed",
                "process_count",
                "max_process_count",
                "remaining_runs",
                "can_reprocess",
                "project_closed",
                "project_closed_at",
                "last_pipeline_run_id",
                "last_pipeline_status",
                "pipeline_succeeded",
                "last_successful_run_id",
            )}
            if reg
            else await registry.get_processing_state(p.drive_folder_id)
        )
        out.append(
            {
                "folder_name": p.folder_name,
                "drive_folder_id": p.drive_folder_id,
                "project_id": p.project_id,
                "folder_path": p.folder_path,
                "metadata": p.metadata,
                **processing,
            }
        )
    return out


def _is_qa_results_path(relative_path: str) -> bool:
    norm = relative_path.strip().casefold()
    if norm in ("qa platform results", "qaplatformresults"):
        return True
    return norm.startswith("qa platform results/") or norm.startswith("qaplatformresults/")


async def get_project_tree(drive_folder_id: str) -> Dict[str, Any]:
    connector = get_google_drive_connector()
    entries = await connector.list_files_recursive(drive_folder_id)
    visible = [
        e
        for e in entries
        if not _is_qa_results_path(e.get("relative_path", e.get("name", "")))
    ]
    folders = sorted({
        PurePosixPath(e.get("relative_path", e["name"])).parent.as_posix()
        for e in visible
        if "/" in e.get("relative_path", "")
    })
    return {
        "drive_folder_id": drive_folder_id,
        "files": [
            {
                "id": e["id"],
                "name": e.get("name"),
                "relative_path": e.get("relative_path", e.get("name")),
                "mime_type": e.get("mimeType"),
                "modified_at": e.get("modifiedTime"),
                "size": e.get("size"),
            }
            for e in visible
        ],
        "folders": folders,
    }


async def _ensure_path(connector, root_folder_id: str, relative_dir: str) -> str:
    if not relative_dir or relative_dir == ".":
        return root_folder_id
    current = root_folder_id
    for segment in PurePosixPath(relative_dir).parts:
        current = await connector.create_folder(current, segment)
    return current


async def create_project_with_files(
    folder_name: str,
    files: List[Tuple[str, bytes, str]],
    *,
    project_id: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    trigger_processing: bool = False,
    mode: str = _DEFAULT_MODE,
) -> Dict[str, Any]:
    connector = get_google_drive_connector()
    system_root = get_system_root_folder_id()
    safe_name = sanitize_folder_name(folder_name)

    existing = await connector.list_child_folders(system_root)
    if any(f.get("name") == safe_name for f in existing):
        raise ValueError(f"Project folder already exists: {safe_name}")

    project_folder_id = await connector.create_folder(system_root, safe_name)
    uploaded = []

    for rel_path, content, mime_type in files:
        rel = sanitize_relative_path(rel_path)
        if not rel:
            continue
        path = PurePosixPath(rel)
        parent_id = await _ensure_path(connector, project_folder_id, path.parent.as_posix())
        file_id = await connector.upload_file(parent_id, path.name, content, mime_type)
        uploaded.append({"relative_path": rel, "file_id": file_id})

    resolved_project_id = project_id or (manifest or {}).get("project_id") or project_id_from_folder_name(safe_name)
    manifest_payload = manifest or {
        "project_id": resolved_project_id,
        "project_name": safe_name,
        "drive_folder_id": project_folder_id,
    }
    manifest_payload.setdefault("project_id", resolved_project_id)
    manifest_payload["drive_folder_id"] = project_folder_id
    await connector.upload_file(
        project_folder_id,
        "manifest.json",
        json.dumps(manifest_payload, indent=2, ensure_ascii=False).encode("utf-8"),
        "application/json",
    )

    result: Dict[str, Any] = {
        "folder_name": safe_name,
        "drive_folder_id": project_folder_id,
        "project_id": resolved_project_id,
        "system_root": get_system_root_name(),
        "files_uploaded": len(uploaded),
        "uploaded": uploaded,
    }

    if trigger_processing:
        run_id = await _run_pipeline_async(
            resolved_project_id,
            project_folder_id,
            safe_name,
            manifest_payload,
            mode=mode,
        )
        result["processing"] = {
            "pipeline_run_id": run_id,
            "pipeline_status": "running",
        }

    return result


async def upload_project_files(
    drive_folder_id: str,
    files: List[Tuple[str, bytes, str]],
    *,
    replace_existing: bool = True,
) -> Dict[str, Any]:
    connector = get_google_drive_connector()
    existing = {e.get("relative_path"): e for e in await connector.list_files_recursive(drive_folder_id)}
    uploaded = []
    replaced = []

    for rel_path, content, mime_type in files:
        rel = sanitize_relative_path(rel_path)
        path = PurePosixPath(rel)
        parent_id = await _ensure_path(connector, drive_folder_id, path.parent.as_posix())

        if replace_existing and rel in existing:
            await connector.update_file(existing[rel]["id"], content, mime_type)
            replaced.append(rel)
        else:
            file_id = await connector.upload_file(parent_id, path.name, content, mime_type)
            uploaded.append({"relative_path": rel, "file_id": file_id})

    return {"uploaded": uploaded, "replaced": replaced}


async def _run_pipeline_async(
    project_id: str,
    drive_folder_id: str,
    folder_name: str,
    metadata: Dict[str, Any],
    mode: str = _DEFAULT_MODE,
    *,
    check_limit: bool = True,
    target_url: Optional[str] = None,
) -> str:
    registry = get_intake_registry()
    await registry.ensure_drive_project_root(drive_folder_id, project_id, folder_name)
    if check_limit:
        await registry.reserve_pipeline_run(drive_folder_id)

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    await registry.record_pipeline_run_started(drive_folder_id, run_id, project_id)
    from src.services.run_tracker import seed_pipeline_status

    seed_pipeline_status(run_id, project_id=project_id, stage="initialized")
    append_run_event(run_id, "run_queued", f"Processing {folder_name}", stage="initialized")

    if target_url:
        from src.services.run_hints import set_run_hints

        set_run_hints(
            run_id,
            {
                "target_url": target_url.strip(),
                "operator_note": f"Operator provided target URL: {target_url.strip()}",
            },
        )
        metadata = {**metadata, "target_url": target_url.strip()}
        append_run_event(
            run_id,
            "operator_hint",
            f"Target URL provided: {target_url.strip()}",
            stage="initialized",
            level="info",
        )

    async def _execute() -> None:
        pipeline_status = "failed"
        results_folder_id: Optional[str] = None
        try:
            await sync_system_root(trigger_run=False, project_folder_name=folder_name)
            documents = await fetch_documents_from_folder(
                drive_folder_id,
                project_id=project_id,
                force_reprocess=True,
            )
            if not documents:
                from src.runtime_policy import RealDataRequiredError

                tree = await get_project_tree(drive_folder_id)
                intake_files = [
                    f for f in tree.get("files", []) if f.get("name") != "manifest.json"
                ]
                if intake_files:
                    raise RealDataRequiredError(
                        f"Found {len(intake_files)} file(s) in Drive folder '{folder_name}' "
                        "but none could be parsed for intake. Check service-account access and "
                        "supported formats (.pdf, .docx, .txt, .md)."
                    )
                raise RealDataRequiredError(
                    f"No intake files in Drive folder '{folder_name}'. "
                    "Upload a requirements PDF or scaffolded project folder, then re-run."
                )

            from src.intake.metadata import enrich_metadata_from_documents

            run_metadata = enrich_metadata_from_documents(
                {**metadata, "drive_folder_id": drive_folder_id},
                documents,
            )
            payload = {
                "run_id": run_id,
                "project_id": run_metadata.get("project_id") or project_id,
                "mode": mode,
                "documents": documents,
                "metadata": run_metadata,
                "drive_folder_id": drive_folder_id,
            }
            result = await ProductQAOrchestrator().run(payload)
            pipeline_status = result.get("status", "failed")
            if pipeline_status == "awaiting_input":
                append_run_event(
                    run_id,
                    "awaiting_input",
                    "Pipeline paused — waiting for operator to provide target URL",
                    stage="awaiting_target_url",
                    level="warn",
                )
            elif pipeline_status == "cancelled":
                append_run_event(
                    run_id,
                    "run_cancelled",
                    "Pipeline cancelled by operator",
                    stage="cancelled",
                    level="warn",
                )
            elif _pipeline_has_deliverables(pipeline_status):
                results_folder_id = await _publish_run_results_to_drive(
                    drive_folder_id, run_id, result, folder_name
                )
            elif pipeline_status != "awaiting_input" and pipeline_status != "cancelled":
                err_msg = result.get("error") or "Pipeline failed during automation"
                append_run_event(run_id, "run_failed", err_msg, stage="failed", level="error")
        except Exception as exc:
            append_run_event(run_id, "run_failed", str(exc), stage="failed", level="error")
            logger.exception("Pipeline failed for %s", folder_name)
        finally:
            try:
                await registry.record_pipeline_finished(
                    drive_folder_id,
                    run_id,
                    pipeline_status,
                    results_drive_folder_id=results_folder_id,
                )
            except Exception as reg_exc:
                logger.warning("Failed to record pipeline status for %s: %s", folder_name, reg_exc)

    asyncio.create_task(_execute())
    return run_id


async def _resume_pipeline_async(
    drive_folder_id: str,
    run_id: str,
    payload: Dict[str, Any],
) -> None:
    """Continue a paused run after the operator supplied target URL."""
    registry = get_intake_registry()
    pipeline_status = "failed"
    results_folder_id: Optional[str] = None
    folder_name = payload.get("folder_name") or drive_folder_id

    async def _execute() -> None:
        nonlocal pipeline_status, results_folder_id
        try:
            result = await ProductQAOrchestrator().run(payload)
            pipeline_status = result.get("status", "failed")
            if _pipeline_has_deliverables(pipeline_status):
                results_folder_id = await _publish_run_results_to_drive(
                    drive_folder_id, run_id, result, folder_name
                )
            elif pipeline_status == "awaiting_input":
                append_run_event(
                    run_id,
                    "awaiting_input",
                    "Pipeline still waiting for target URL",
                    stage="awaiting_target_url",
                    level="warn",
                )
            elif pipeline_status == "cancelled":
                append_run_event(
                    run_id,
                    "run_cancelled",
                    "Pipeline cancelled by operator",
                    stage="cancelled",
                    level="warn",
                )
            else:
                err_msg = result.get("error") or "Pipeline failed during automation"
                append_run_event(run_id, "run_failed", err_msg, stage="failed", level="error")
        except Exception as exc:
            append_run_event(run_id, "run_failed", str(exc), stage="failed", level="error")
            logger.exception("Pipeline resume failed for %s", folder_name)
        finally:
            try:
                await registry.record_pipeline_finished(
                    drive_folder_id,
                    run_id,
                    pipeline_status,
                    results_drive_folder_id=results_folder_id,
                )
            except Exception as reg_exc:
                logger.warning("Failed to record pipeline status for %s: %s", folder_name, reg_exc)

    asyncio.create_task(_execute())


async def trigger_project_processing(
    drive_folder_id: str,
    *,
    mode: str = _DEFAULT_MODE,
    force_reprocess: bool = False,
    target_url: Optional[str] = None,
) -> Dict[str, Any]:
    projects = await list_project_folders_under_system_root()
    match = next((p for p in projects if p.drive_folder_id == drive_folder_id), None)
    if not match:
        raise ValueError("Project folder not found under APTP")

    registry = get_intake_registry()
    await registry.ensure_drive_project_root(match.drive_folder_id, match.project_id, match.folder_name)
    state = await registry.get_processing_state(drive_folder_id)
    last_run = state.get("last_pipeline_run_id")
    last_status = state.get("last_pipeline_status")

    from src.services.pipeline_checkpoint import is_awaiting_target_url

    if last_run and last_status == "awaiting_input" and is_awaiting_target_url(last_run):
        effective_url = (target_url or "").strip() or (match.metadata or {}).get("target_url")
        if effective_url:
            return await provide_target_url_and_resume(
                drive_folder_id,
                last_run,
                target_url=effective_url,
            )
        return {
            "project_id": match.project_id,
            "folder_name": match.folder_name,
            "pipeline_run_id": last_run,
            "pipeline_status": "awaiting_input",
            "reused_paused_run": True,
            **state,
        }

    await registry.assert_can_process(drive_folder_id)

    if force_reprocess:
        await sync_system_root(trigger_run=False, project_folder_name=match.folder_name, force_reprocess=True)

    run_id = await _run_pipeline_async(
        match.project_id,
        match.drive_folder_id,
        match.folder_name,
        match.metadata,
        mode=mode,
        check_limit=True,
        target_url=target_url,
    )
    processing = await registry.get_processing_state(drive_folder_id)
    return {
        "project_id": match.project_id,
        "folder_name": match.folder_name,
        "pipeline_run_id": run_id,
        "pipeline_status": "running",
        **processing,
    }


async def provide_target_url_and_resume(
    drive_folder_id: str,
    run_id: str,
    *,
    target_url: str,
) -> Dict[str, Any]:
    """Save operator target URL and resume a paused pipeline from intake interpretation."""
    from src.services.pipeline_checkpoint import is_awaiting_target_url, load_checkpoint

    if not is_awaiting_target_url(run_id):
        raise ValueError("Run is not waiting for target URL")

    projects = await list_project_folders_under_system_root()
    match = next((p for p in projects if p.drive_folder_id == drive_folder_id), None)
    if not match:
        raise ValueError("Project folder not found under APTP")

    checkpoint = load_checkpoint(run_id)
    if not checkpoint:
        raise ValueError("Pipeline checkpoint not found for run")

    from src.services.run_hints import set_run_hints

    trimmed = target_url.strip()
    if not trimmed.startswith("http"):
        raise ValueError("target_url must start with http:// or https://")

    hints = set_run_hints(
        run_id,
        {
            "target_url": trimmed,
            "operator_note": f"Operator provided target URL: {trimmed}",
        },
    )
    append_run_event(
        run_id,
        "operator_hint",
        f"Target URL provided: {trimmed}",
        stage="awaiting_target_url",
        level="info",
    )

    payload = {
        "run_id": run_id,
        "project_id": checkpoint.get("project_id") or match.project_id,
        "mode": checkpoint.get("mode", _DEFAULT_MODE),
        "metadata": {**(checkpoint.get("metadata") or {}), "target_url": trimmed},
        "drive_folder_id": drive_folder_id,
        "resume_from": "intake_interpretation",
        "folder_name": match.folder_name,
    }

    registry = get_intake_registry()
    await registry.mark_pipeline_resumed(drive_folder_id, run_id)
    await _resume_pipeline_async(drive_folder_id, run_id, payload)

    return {
        "run_id": run_id,
        "drive_folder_id": drive_folder_id,
        "status": "running",
        "target_url": trimmed,
        "hints": hints,
    }


async def apply_runtime_hints(
    drive_folder_id: str,
    run_id: str,
    *,
    target_url: str,
) -> Dict[str, Any]:
    """Provide target URL for a paused run and resume the pipeline."""
    return await provide_target_url_and_resume(
        drive_folder_id,
        run_id,
        target_url=target_url,
    )


async def close_project(drive_folder_id: str) -> Dict[str, Any]:
    projects = await list_project_folders_under_system_root()
    match = next((p for p in projects if p.drive_folder_id == drive_folder_id), None)
    if not match:
        raise ValueError("Project folder not found under APTP")
    registry = get_intake_registry()
    await registry.ensure_drive_project_root(match.drive_folder_id, match.project_id, match.folder_name)
    state = await registry.close_project(drive_folder_id)
    return {
        "project_id": match.project_id,
        "folder_name": match.folder_name,
        "drive_folder_id": drive_folder_id,
        **state,
    }


async def list_project_qa_results(drive_folder_id: str) -> Dict[str, Any]:
    connector = get_google_drive_connector()
    registry = get_intake_registry()
    processing = await registry.get_processing_state(drive_folder_id)
    results_root_id = await find_qa_results_root(connector, drive_folder_id)

    project_id: Optional[str] = None
    try:
        projects = await list_project_folders_under_system_root()
        match = next((p for p in projects if p.drive_folder_id == drive_folder_id), None)
        project_id = match.project_id if match else None
    except Exception:
        pass

    drive_runs: List[Dict[str, Any]] = []
    if results_root_id:
        for child in await connector.list_child_folders(results_root_id):
            run_id = child.get("name", "")
            run_folder_id = child["id"]
            entries = await connector.list_files_recursive(run_folder_id)
            files = [
                {
                    "id": e["id"],
                    "name": e.get("name"),
                    "relative_path": e.get("relative_path", e.get("name")),
                    "mime_type": e.get("mimeType"),
                    "modified_at": e.get("modifiedTime"),
                    "size": e.get("size"),
                }
                for e in entries
                if e.get("mimeType") != "application/vnd.google-apps.folder"
            ]
            from src.services.run_history import is_internal_artifact_name

            publishable_files = [f for f in files if not is_internal_artifact_name(f.get("name") or "")]
            manifest_entry = next((f for f in files if f["name"] == "manifest.json"), None)
            drive_runs.append(
                {
                    "run_id": run_id,
                    "status": "completed",
                    "source": "drive",
                    "drive_folder_id": run_folder_id,
                    "file_count": len(publishable_files),
                    "has_reports": any(f["name"] == "reports.json" for f in publishable_files),
                    "has_scripts": any("scripts/" in f.get("relative_path", "") for f in files),
                    "has_evidence": any(
                        "evidence/" in f.get("relative_path", "") or f["name"] == "execution-report.pdf"
                        for f in files
                    ),
                    "manifest_file_id": manifest_entry["id"] if manifest_entry else None,
                    "files": files,
                }
            )

    db_runs = await registry.list_pipeline_runs(drive_folder_id)
    local_runs = list_local_artifact_runs(project_id) if project_id else []
    runs = merge_run_histories(db_runs, local_runs, drive_runs)

    if (processing.get("process_count") or 0) == 0 and not processing.get("processed"):
        runs = [run for run in runs if not run_looks_like_unscheduled_probe(run)]

    from src.services.pipeline_checkpoint import is_resumable, load_resume_checkpoint

    for run in runs:
        rid = run.get("run_id", "")
        if rid and is_resumable(rid):
            checkpoint = load_resume_checkpoint(rid) or {}
            run["resumable"] = True
            run["resume_from"] = checkpoint.get("failed_stage")
        else:
            run["resumable"] = False

    if not runs and processing.get("last_pipeline_run_id"):
        last_id = processing["last_pipeline_run_id"]
        entry = {
            "run_id": last_id,
            "status": processing.get("last_pipeline_status") or "unknown",
            "source": "platform",
            "file_count": 0,
            "has_reports": False,
            "has_scripts": False,
            "has_evidence": False,
            "manifest_file_id": None,
            "files": [],
        }
        if is_resumable(last_id):
            checkpoint = load_resume_checkpoint(last_id) or {}
            entry["resumable"] = True
            entry["resume_from"] = checkpoint.get("failed_stage")
        else:
            entry["resumable"] = False
        runs = [entry]

    return {
        "drive_folder_id": drive_folder_id,
        "project_id": project_id,
        "qa_results_folder": QA_RESULTS_FOLDER,
        "results_root_id": results_root_id,
        **processing,
        "runs": runs,
    }


async def get_project_qa_run_reports(drive_folder_id: str, run_id: str) -> Dict[str, Any]:
    connector = get_google_drive_connector()
    results_root_id = await find_qa_results_root(connector, drive_folder_id)

    if results_root_id:
        run_folder = next(
            (f for f in await connector.list_child_folders(results_root_id) if f.get("name") == run_id),
            None,
        )
        if run_folder:
            entries = await connector.list_folder_children(run_folder["id"])
            reports: Dict[str, Any] = {}
            summary: Dict[str, Any] = {}

            for entry in entries:
                if entry.get("mimeType") == "application/vnd.google-apps.folder":
                    continue
                name = entry.get("name", "")
                if name not in ("reports.json", "pipeline_summary.json"):
                    continue
                raw = await connector.download_file(entry["id"])
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError(f"Invalid {name} in Drive") from exc
                if name == "reports.json":
                    reports = parsed
                else:
                    summary = parsed

            if reports or summary:
                return {
                    "drive_folder_id": drive_folder_id,
                    "run_id": run_id,
                    "run_folder_id": run_folder["id"],
                    "source": "drive",
                    "reports": reports,
                    "pipeline_summary": summary,
                }

    local = load_local_run_reports(run_id)
    if local:
        local["drive_folder_id"] = drive_folder_id
        return local

    from src.memory.enhanced_memory import get_memory

    summary = get_memory().read(f"pipeline_summary:{run_id}")
    if summary:
        reports = (summary.get("results") or {}).get("reports") or {}
        return {
            "drive_folder_id": drive_folder_id,
            "run_id": run_id,
            "run_folder_id": None,
            "source": "memory",
            "reports": reports,
            "pipeline_summary": summary,
        }

    raise ValueError(f"Run not found: {run_id}")


async def delete_project_file(file_id: str) -> None:
    await get_google_drive_connector().delete_file(file_id)


async def delete_project_folder(drive_folder_id: str) -> None:
    await get_google_drive_connector().delete_file(drive_folder_id)
