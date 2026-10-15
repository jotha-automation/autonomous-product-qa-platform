import asyncio
import json
import mimetypes
import uuid
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from src.api.deps import (
    assert_run_project_scope,
    resolve_run_project_id,
    verify_api_key,
    verify_run_browser_resource,
)
from src.api.schemas import RunRequest, RunResponse
from src.memory.enhanced_memory import get_memory
from src.orchestrator.engine import ProductQAOrchestrator
from src.services.artifact_bundle import build_run_artifact_zip, get_run_manifest
from src.services.pipeline_checkpoint import is_resumable
from src.services.run_access_token import issue_run_access_token
from src.services.run_cancellation import request_cancel
from src.services.run_tracker import (
    append_run_event,
    deliver_run_artifacts,
    get_run_events,
    run_terminal_event_emitted,
)

router = APIRouter(prefix="/runs", tags=["runs"])


def get_orchestrator() -> ProductQAOrchestrator:
    """Return an orchestrator instance. Safe for concurrent runs via contextvar run scope."""
    return ProductQAOrchestrator()


_orchestrator = get_orchestrator()


async def _execute_run(payload: dict) -> None:
    run_id = payload["run_id"]
    orchestrator = get_orchestrator()
    try:
        result = await orchestrator.run(payload)
        if result.get("status") in {"awaiting_input", "cancelled"}:
            return
        if run_terminal_event_emitted(get_run_events(run_id)):
            return
        deliver_run_artifacts(run_id, result)
    except Exception as exc:
        append_run_event(
            run_id,
            "run_failed",
            str(exc),
            stage="failed",
            level="error",
        )


@router.post("", response_model=RunResponse, dependencies=[Depends(verify_api_key)])
async def create_run(body: RunRequest):
    result = await _orchestrator.run(body.model_dump())
    return RunResponse(
        run_id=result["run_id"],
        status=result["status"],
        stage=result.get("stage", "unknown"),
        duration_seconds=result.get("duration_seconds", 0),
        results=result.get("results", {}),
        error=result.get("error"),
    )


@router.post("/async", dependencies=[Depends(verify_api_key)])
async def create_run_async(body: RunRequest, background_tasks: BackgroundTasks):
    payload = body.model_dump()
    run_id = payload.get("run_id") or f"run-{uuid.uuid4().hex[:12]}"
    payload["run_id"] = run_id
    from src.services.run_tracker import seed_pipeline_status

    seed_pipeline_status(run_id, project_id=payload.get("project_id"), stage="initialized")
    append_run_event(run_id, "run_queued", "Pipeline queued", stage="initialized")
    background_tasks.add_task(_execute_run, payload)
    return {"run_id": run_id, "status": "running"}


async def _execute_resume(run_id: str, resume_from: str | None = None) -> None:
    orchestrator = get_orchestrator()
    try:
        from src.services.intake_registry import get_intake_registry
        from src.services.pipeline_checkpoint import load_resume_checkpoint

        checkpoint = load_resume_checkpoint(run_id)
        drive_folder_id = (checkpoint or {}).get("metadata", {}).get("drive_folder_id")
        if drive_folder_id:
            await get_intake_registry().mark_pipeline_resumed(drive_folder_id, run_id)

        result = await orchestrator.resume_run(run_id, resume_from=resume_from)
        if result.get("status") in {"awaiting_input", "cancelled"}:
            return
        if run_terminal_event_emitted(get_run_events(run_id)):
            return
        if result.get("status") == "failed":
            append_run_event(
                run_id,
                "run_failed",
                result.get("error") or "Pipeline resume failed",
                stage="failed",
                level="error",
            )
            return
        deliver_run_artifacts(run_id, result)
    except Exception as exc:
        append_run_event(run_id, "run_failed", str(exc), stage="failed", level="error")


@router.post("/{run_id}/cancel", dependencies=[Depends(verify_api_key)])
async def cancel_run_route(run_id: str):
    try:
        result = request_cancel(run_id, get_status=_orchestrator.get_pipeline_status)
    except ValueError as exc:
        message = str(exc)
        if message == "Run not found":
            raise HTTPException(404, message) from exc
        raise HTTPException(409, message) from exc

    from src.services.intake_registry import get_intake_registry

    registry = get_intake_registry()
    drive_folder_id = await registry.get_run_drive_folder_id(run_id)
    if drive_folder_id:
        await registry.record_pipeline_finished(drive_folder_id, run_id, "cancelled")

    return result


@router.get("/{run_id}/resumable", dependencies=[Depends(verify_api_key)])
async def get_run_resumable(run_id: str):
    from src.services.pipeline_checkpoint import load_resume_checkpoint

    checkpoint = load_resume_checkpoint(run_id)
    status = _orchestrator.get_pipeline_status(run_id)
    if not checkpoint and not status:
        raise HTTPException(404, "Run not found")
    return {
        "run_id": run_id,
        "resumable": is_resumable(run_id),
        "resume_from": (checkpoint or {}).get("failed_stage")
        or status.get("failed_at_stage")
        or status.get("resume_from"),
        "status": (status or {}).get("status"),
    }


@router.post("/{run_id}/resume", dependencies=[Depends(verify_api_key)])
async def resume_run_route(run_id: str, background_tasks: BackgroundTasks, resume_from: str | None = None):
    if not is_resumable(run_id):
        raise HTTPException(409, f"Run {run_id} is not resumable")
    append_run_event(run_id, "run_queued", "Pipeline resume queued", stage="initialized")
    background_tasks.add_task(_execute_resume, run_id, resume_from)
    return {"run_id": run_id, "status": "running", "resumed": True}


@router.post("/{run_id}/resume/sync", response_model=RunResponse, dependencies=[Depends(verify_api_key)])
async def resume_run_sync(run_id: str, resume_from: str | None = None):
    if not is_resumable(run_id):
        raise HTTPException(409, f"Run {run_id} is not resumable")
    from src.services.intake_registry import get_intake_registry
    from src.services.pipeline_checkpoint import load_resume_checkpoint

    checkpoint = load_resume_checkpoint(run_id)
    drive_folder_id = (checkpoint or {}).get("metadata", {}).get("drive_folder_id")
    if drive_folder_id:
        await get_intake_registry().mark_pipeline_resumed(drive_folder_id, run_id)

    result = await _orchestrator.resume_run(run_id, resume_from=resume_from)
    return RunResponse(
        run_id=result["run_id"],
        status=result["status"],
        stage=result.get("stage", "unknown"),
        duration_seconds=result.get("duration_seconds", 0),
        results=result.get("results", {}),
        error=result.get("error"),
    )


@router.get("/{run_id}", dependencies=[Depends(verify_api_key)])
async def get_run(run_id: str):
    status = _orchestrator.get_pipeline_status(run_id)
    if not status:
        raise HTTPException(404, "Run not found")
    summary = get_memory().read(f"pipeline_summary:{run_id}")
    if summary:
        status = {**status, **summary}
    status["events"] = get_run_events(run_id)
    status["resumable"] = is_resumable(run_id)
    return status


@router.get("/{run_id}/access-token", dependencies=[Depends(verify_api_key)])
async def get_run_access_token(run_id: str):
    status = _orchestrator.get_pipeline_status(run_id)
    if not status:
        raise HTTPException(404, "Run not found")
    project_id = await resolve_run_project_id(run_id)
    return issue_run_access_token(run_id, project_id=project_id)


@router.get("/{run_id}/events", dependencies=[Depends(verify_api_key)])
async def get_run_events_route(run_id: str):
    events = get_run_events(run_id)
    if not events and not _orchestrator.get_pipeline_status(run_id):
        raise HTTPException(404, "Run not found")
    return {"run_id": run_id, "events": events}


@router.get("/{run_id}/stream", dependencies=[Depends(verify_run_browser_resource)])
async def stream_run(run_id: str):
    async def _stream_should_finish(events: list, status: dict) -> bool:
        run_status = status.get("status", "")
        stage = status.get("stage", "")
        if run_status == "awaiting_input" or stage == "awaiting_target_url":
            return False
        if run_terminal_event_emitted(events):
            return True
        if run_status in {"failed", "cancelled"} and stage in {"failed", "cancelled"}:
            return any(e.get("type") in {"run_failed", "run_cancelled"} for e in events)
        return False

    async def event_generator() -> AsyncGenerator[str, None]:
        seen = 0
        for _ in range(3600):
            events = get_run_events(run_id)
            while seen < len(events):
                yield f"data: {json.dumps(events[seen])}\n\n"
                seen += 1
            status = _orchestrator.get_pipeline_status(run_id) or {}
            if await _stream_should_finish(events, status):
                summary = get_memory().read(f"pipeline_summary:{run_id}") or status
                manifest: dict = {}
                try:
                    manifest = get_run_manifest(run_id)
                except FileNotFoundError:
                    pass
                reports = (summary.get("results") or {}).get("reports")
                artifacts_ready = any(e.get("type") == "artifacts_ready" for e in events)
                yield f"data: {json.dumps({'type': 'done', 'status': summary, 'reports_ready': bool(reports or artifacts_ready), 'artifacts': manifest})}\n\n"
                break
            await asyncio.sleep(1)
        else:
            yield f"data: {json.dumps({'type': 'timeout'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/reports", dependencies=[Depends(verify_api_key), Depends(assert_run_project_scope)])
async def get_run_reports(run_id: str):
    summary = get_memory().read(f"pipeline_summary:{run_id}")
    if summary:
        reports = (summary.get("results") or {}).get("reports")
        if reports:
            from src.reporting.builders import enrich_reports_from_results

            results = summary.get("results") or {}
            reports = enrich_reports_from_results(reports, results, run_id, summary)
            return reports

    from src.services.artifact_bundle import find_artifact_dir_on_disk

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if artifact_dir:
        reports_path = artifact_dir / "reports.json"
        if reports_path.is_file():
            reports = json.loads(reports_path.read_text(encoding="utf-8"))
            if reports:
                from src.reporting.builders import enrich_reports_from_results
                from src.services.artifact_bundle import load_run_summary

                try:
                    disk_summary = load_run_summary(run_id)
                    results = disk_summary.get("results") or {}
                    reports = enrich_reports_from_results(reports, results, run_id, disk_summary)
                except FileNotFoundError:
                    pass
                return reports

    raise HTTPException(404, "Run not found")


@router.get("/{run_id}/visual-report", dependencies=[Depends(verify_api_key), Depends(assert_run_project_scope)])
async def get_visual_report(run_id: str):
    from src.reporting.builders import build_visual_report_for_run, enrich_reports_from_results
    from src.services.artifact_bundle import find_artifact_dir_on_disk, load_run_summary

    try:
        summary = load_run_summary(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    results = summary.get("results") or {}
    reports = results.get("reports")
    if not reports:
        artifact_dir = find_artifact_dir_on_disk(run_id)
        if artifact_dir:
            reports_path = artifact_dir / "reports.json"
            if reports_path.is_file():
                reports = json.loads(reports_path.read_text(encoding="utf-8"))
    if reports:
        reports = enrich_reports_from_results(reports, results, run_id, summary)

    try:
        return build_visual_report_for_run(run_id, summary, reports=reports)
    except Exception as exc:
        raise HTTPException(404, f"Visual report unavailable: {exc}") from exc


@router.get("/{run_id}/evidence/{asset_path:path}", dependencies=[Depends(verify_run_browser_resource)])
async def get_evidence_asset(
    run_id: str,
    asset_path: str,
):
    """Serve individual evidence assets (screenshots, videos, PDF) for inline UI viewing."""
    from src.reporting.visual_report import resolve_evidence_file

    resolved = resolve_evidence_file(run_id, asset_path)
    if not resolved:
        raise HTTPException(404, "Evidence asset not found")

    media_type, _ = mimetypes.guess_type(resolved.name)
    if resolved.suffix.lower() == ".webm":
        media_type = "video/webm"
    elif resolved.suffix.lower() == ".pdf":
        media_type = "application/pdf"
    elif resolved.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        media_type = media_type or "image/png"

    return FileResponse(
        path=resolved,
        media_type=media_type or "application/octet-stream",
        filename=resolved.name,
    )


@router.get("/{run_id}/artifacts/manifest", dependencies=[Depends(verify_api_key), Depends(assert_run_project_scope)])
async def get_run_artifact_manifest(run_id: str):
    try:
        return get_run_manifest(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get(
    "/{run_id}/artifacts/deliverable.xlsx",
    dependencies=[Depends(verify_run_browser_resource)],
)
async def download_deliverable_xlsx(run_id: str):
    from src.services.artifact_bundle import find_artifact_dir_on_disk, load_run_summary

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if not artifact_dir:
        try:
            summary = load_run_summary(run_id)
            path = (summary.get("results") or {}).get("deliverable", {}).get("deliverable_path")
            if path and Path(path).is_file():
                content = Path(path).read_bytes()
                return Response(
                    content=content,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="deliverable.xlsx"'},
                )
        except FileNotFoundError:
            pass
        raise HTTPException(404, "Deliverable not found for this run")

    deliverable = artifact_dir / "deliverable.xlsx"
    if not deliverable.is_file():
        raise HTTPException(404, "Deliverable not found for this run")
    return Response(
        content=deliverable.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="deliverable.xlsx"'},
    )


@router.get("/{run_id}/artifacts", dependencies=[Depends(verify_run_browser_resource)])
async def download_run_artifacts(
    run_id: str,
    bundle: str = Query("full", pattern="^(full|scripts|evidence)$"),
):
    """Download Playwright scripts, evidence, or full artifact bundle as .zip."""
    try:
        content, filename = await asyncio.to_thread(build_run_artifact_zip, run_id, bundle)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
