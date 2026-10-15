import json
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.deps import verify_api_key
from src.connectors.google_drive.errors import raise_for_drive_error
from src.intake.file_types import guess_mime_from_filename
from src.intake.guardrails import format_violation_messages, validate_upload_batch
from src.services import drive_manager

router = APIRouter(prefix="/drive", tags=["drive"])
_DEFAULT_PIPELINE_MODE = os.getenv("APTP_DEFAULT_PIPELINE_MODE", "full")


@router.get("/projects", dependencies=[Depends(verify_api_key)])
async def list_drive_projects():
    try:
        return {"projects": await drive_manager.list_projects()}
    except Exception as exc:
        raise_for_drive_error(exc)


@router.get("/projects/{drive_folder_id}/tree", dependencies=[Depends(verify_api_key)])
async def get_project_tree(drive_folder_id: str):
    try:
        return await drive_manager.get_project_tree(drive_folder_id)
    except Exception as exc:
        raise_for_drive_error(exc)


@router.get("/projects/{drive_folder_id}/qa-results", dependencies=[Depends(verify_api_key)])
async def list_project_qa_results(drive_folder_id: str):
    try:
        return await drive_manager.list_project_qa_results(drive_folder_id)
    except Exception as exc:
        raise_for_drive_error(exc)


@router.get(
    "/projects/{drive_folder_id}/qa-results/{run_id}",
    dependencies=[Depends(verify_api_key)],
)
async def get_project_qa_run_reports(drive_folder_id: str, run_id: str):
    try:
        return await drive_manager.get_project_qa_run_reports(drive_folder_id, run_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise_for_drive_error(exc)


@router.post("/projects", dependencies=[Depends(verify_api_key)])
async def create_project(
    folder_name: str = Form(...),
    project_id: Optional[str] = Form(None),
    target_url: Optional[str] = Form(None),
    trigger_processing: bool = Form(False),
    mode: str = Form(_DEFAULT_PIPELINE_MODE),
    files: List[UploadFile] = File(...),
    relative_paths: List[str] = Form(...),
):
    if len(files) != len(relative_paths):
        raise HTTPException(400, "files and relative_paths length mismatch")
    if not files:
        raise HTTPException(400, "At least one file is required")

    manifest = None
    if project_id or target_url:
        manifest = {"project_id": project_id or drive_manager.project_id_from_folder_name(folder_name)}
        if target_url:
            manifest["target_url"] = target_url

    payload = []
    for upload, rel in zip(files, relative_paths):
        content = await upload.read()
        payload.append((rel, content, guess_mime_from_filename(upload.filename or rel)))

    guardrail_result = validate_upload_batch(payload)
    if not guardrail_result.accepted:
        raise HTTPException(400, format_violation_messages(guardrail_result))

    try:
        result = await drive_manager.create_project_with_files(
            folder_name,
            payload,
            project_id=project_id,
            manifest=manifest,
            trigger_processing=trigger_processing,
            mode=mode,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise_for_drive_error(exc)

    return result


@router.post("/projects/{drive_folder_id}/files", dependencies=[Depends(verify_api_key)])
async def upload_files(
    drive_folder_id: str,
    files: List[UploadFile] = File(...),
    relative_paths: List[str] = Form(...),
    replace_existing: bool = Form(True),
):
    if len(files) != len(relative_paths):
        raise HTTPException(400, "files and relative_paths length mismatch")
    payload = []
    for upload, rel in zip(files, relative_paths):
        content = await upload.read()
        payload.append((rel, content, guess_mime_from_filename(upload.filename or rel)))
    guardrail_result = validate_upload_batch(payload)
    if not guardrail_result.accepted:
        raise HTTPException(400, format_violation_messages(guardrail_result))
    try:
        return await drive_manager.upload_project_files(
            drive_folder_id, payload, replace_existing=replace_existing
        )
    except Exception as exc:
        raise_for_drive_error(exc)


@router.delete("/files/{file_id}", dependencies=[Depends(verify_api_key)])
async def delete_file(file_id: str):
    try:
        await drive_manager.delete_project_file(file_id)
    except Exception as exc:
        raise_for_drive_error(exc)
    return {"deleted": file_id}


@router.delete("/projects/{drive_folder_id}", dependencies=[Depends(verify_api_key)])
async def delete_project(drive_folder_id: str):
    try:
        await drive_manager.delete_project_folder(drive_folder_id)
    except Exception as exc:
        raise_for_drive_error(exc)
    return {"deleted": drive_folder_id}


@router.post("/projects/{drive_folder_id}/process", dependencies=[Depends(verify_api_key)])
async def process_project(
    drive_folder_id: str,
    payload: dict | None = None,
):
    body = payload or {}
    try:
        return await drive_manager.trigger_project_processing(
            drive_folder_id,
            mode=body.get("mode", _DEFAULT_PIPELINE_MODE),
            force_reprocess=body.get("force_reprocess", False),
            target_url=body.get("target_url"),
        )
    except ValueError as exc:
        msg = str(exc)
        if "Maximum processing limit" in msg or "closed and delivered" in msg:
            raise HTTPException(409, msg) from exc
        raise HTTPException(404, msg) from exc


@router.post("/projects/{drive_folder_id}/runtime-hints", dependencies=[Depends(verify_api_key)])
async def set_project_runtime_hints(drive_folder_id: str, payload: dict | None = None):
    """Provide target URL for a paused run and resume the pipeline from intake interpretation."""
    body = payload or {}
    run_id = body.get("run_id")
    target_url = (body.get("target_url") or "").strip()
    if not run_id:
        raise HTTPException(400, "run_id is required")
    if not target_url:
        raise HTTPException(400, "target_url is required")
    try:
        return await drive_manager.provide_target_url_and_resume(
            drive_folder_id, run_id, target_url=target_url
        )
    except ValueError as exc:
        msg = str(exc)
        if "not waiting" in msg.lower() or "checkpoint" in msg.lower():
            raise HTTPException(409, msg) from exc
        raise HTTPException(404, msg) from exc


@router.post("/projects/{drive_folder_id}/close", dependencies=[Depends(verify_api_key)])
async def close_project(drive_folder_id: str):
    try:
        return await drive_manager.close_project(drive_folder_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise_for_drive_error(exc)
