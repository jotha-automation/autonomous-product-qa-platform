import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from src.api.schemas import RunResponse
from src.connectors.google_drive.connector import get_google_drive_connector
from src.connectors.google_drive.intake import fetch_documents_by_ids
from src.connectors.google_drive.system_root import sync_system_root
from src.events.bus import EventType, PlatformEvent, get_event_bus
from src.orchestrator.engine import ProductQAOrchestrator

logger = logging.getLogger("webhooks_google_drive")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
_connector = get_google_drive_connector()
_orchestrator = ProductQAOrchestrator()


def _verify_webhook_access(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_goog_channel_token: str | None = Header(default=None),
    x_goog_channel_id: str | None = Header(default=None),
    x_webhook_secret: str | None = Header(default=None),
) -> None:
    """Google push uses X-Goog-Channel-Token; manual calls use X-Api-Key or X-Webhook-Secret."""
    expected_secret = (os.getenv("GOOGLE_DRIVE_WEBHOOK_SECRET") or "").strip()
    expected_api_key = (os.getenv("APTP_API_KEY") or "").strip()

    if x_goog_channel_id or x_goog_channel_token:
        if not expected_secret:
            raise HTTPException(status_code=500, detail="GOOGLE_DRIVE_WEBHOOK_SECRET not configured")
        if (x_goog_channel_token or "") != expected_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
        return

    if expected_secret and (x_webhook_secret or "") == expected_secret:
        return
    if expected_api_key and x_api_key == expected_api_key:
        return
    if not expected_api_key and not expected_secret:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.post("/google-drive", dependencies=[Depends(_verify_webhook_access)])
async def google_drive_webhook(request: Request, payload: dict | None = None):
    """Sync APTP system root (default) or a single project folder, then optionally run QA."""
    payload = payload or {}
    goog_headers = {
        "X-Goog-Resource-State": request.headers.get("X-Goog-Resource-State"),
        "X-Goog-Channel-ID": request.headers.get("X-Goog-Channel-ID"),
        "X-Goog-Resource-ID": request.headers.get("X-Goog-Resource-ID"),
    }
    is_google_push = bool(goog_headers["X-Goog-Channel-ID"])
    if is_google_push:
        payload = {**payload, **{k: v for k, v in goog_headers.items() if v}}
        if goog_headers["X-Goog-Resource-State"] == "sync":
            logger.info("Google Drive watch channel verified (sync)")
            return {"status": "ok", "message": "watch channel active"}

    trigger_run = payload.get("trigger_run", True)
    mode = payload.get("mode", "full")
    force_reprocess = payload.get("force_reprocess", False)
    project_folder_name = payload.get("project_folder_name")
    folder_id = payload.get("drive_folder_id") or payload.get("folder_id")
    use_system_root = payload.get("use_system_root", not bool(folder_id))

    bus = get_event_bus()

    if use_system_root:
        try:
            result = await sync_system_root(
                trigger_run=trigger_run,
                mode=mode,
                force_reprocess=force_reprocess,
                project_folder_name=project_folder_name,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        if not trigger_run:
            return RunResponse(
                run_id="none",
                status="system_sync",
                stage="intake",
                results={
                    "project_folders_found": result.project_folders_found,
                    "new_project_folders": result.new_project_folders,
                    "projects": [p.__dict__ for p in result.projects],
                },
            )

        runs = [p for p in result.projects if p.pipeline_run_id]
        if not runs:
            return RunResponse(
                run_id="none",
                status="no_changes",
                stage="intake",
                results={
                    "project_folders_found": result.project_folders_found,
                    "message": "No project folders had files to process",
                },
            )

        last = runs[-1]
        return RunResponse(
            run_id=last.pipeline_run_id or "none",
            status=last.pipeline_status or "completed",
            stage="done",
            results={
                "system_root": result.system_root_name,
                "projects_processed": len(runs),
                "runs": [
                    {
                        "project_id": p.project_id,
                        "folder_name": p.folder_name,
                        "run_id": p.pipeline_run_id,
                        "status": p.pipeline_status,
                    }
                    for p in runs
                ],
            },
        )

    project_id = payload.get("project_id") or os.getenv("GOOGLE_DRIVE_DEFAULT_PROJECT_ID", "demo")
    from src.connectors.google_drive.intake import ingest_from_sync_result, sync_drive_intake
    from src.services.intake_registry import get_intake_registry

    documents = []
    sync = None
    file_ids = await _connector.handle_webhook(payload)

    if file_ids and not folder_id:
        documents = await fetch_documents_by_ids(file_ids, project_id=project_id, connector=_connector)
    elif folder_id:
        sync = await sync_drive_intake(
            project_id,
            folder_id,
            connector=_connector,
            force_reprocess=force_reprocess,
        )
        documents = await ingest_from_sync_result(sync, connector=_connector)

    for doc in documents:
        await bus.publish(
            PlatformEvent(
                event_type=EventType.DOCUMENT_UPLOADED,
                correlation_id=project_id,
                payload={"file_id": doc.get("drive_file_id"), "document": doc},
            )
        )

    if not trigger_run:
        return RunResponse(
            run_id="none",
            status="intake_only",
            stage="intake",
            results={"documents": len(documents)},
        )

    if not documents:
        raise HTTPException(400, "No documents found for the specified folder")

    metadata = payload.get("metadata", {})
    result = await _orchestrator.run(
        {"project_id": project_id, "mode": mode, "documents": documents, "metadata": metadata}
    )

    if sync and sync.root_id:
        await get_intake_registry().mark_files_processed(
            sync.root_id,
            project_id,
            [d.get("relative_path", "") for d in documents if d.get("relative_path")],
            run_id=result.get("run_id"),
        )

    return RunResponse(
        run_id=result["run_id"],
        status=result["status"],
        stage=result.get("stage", "unknown"),
        duration_seconds=result.get("duration_seconds", 0),
        results=result.get("results", {}),
        error=result.get("error"),
    )
