"""Intake registry API — system root APTP, project folders, changelog."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import verify_api_key
from src.connectors.google_drive.intake import sync_drive_intake, sync_local_intake
from src.connectors.google_drive.system_root import (
    get_system_root_folder_id,
    get_system_root_name,
    list_project_folders_under_system_root,
    sync_system_root,
)
from src.runtime_policy import drive_enabled
from src.services.intake_registry import get_intake_registry

router = APIRouter(prefix="/intake", tags=["intake"])


@router.get("/system", dependencies=[Depends(verify_api_key)])
async def get_system_root():
    """Configured APTP system root folder."""
    if not drive_enabled():
        return {
            "system_root_name": get_system_root_name(),
            "system_root_folder_id": None,
            "stored": None,
            "drive_enabled": False,
        }

    registry = get_intake_registry()
    stored = await registry.get_system_root()
    try:
        folder_id = get_system_root_folder_id()
    except ValueError:
        folder_id = None
    return {
        "system_root_name": get_system_root_name(),
        "system_root_folder_id": folder_id,
        "stored": stored,
    }


@router.get("/projects", dependencies=[Depends(verify_api_key)])
async def list_projects():
    """Project folders indexed under APTP (from DB + live Drive preview)."""
    registry = get_intake_registry()
    indexed = await registry.list_project_folders()
    live = []
    if not drive_enabled():
        return {"indexed": indexed, "live_drive": live, "drive_enabled": False}

    try:
        live_folders = await list_project_folders_under_system_root()
        live = [
            {
                "folder_name": p.folder_name,
                "drive_folder_id": p.drive_folder_id,
                "project_id": p.project_id,
                "folder_path": p.folder_path,
                "metadata": p.metadata,
            }
            for p in live_folders
        ]
    except ValueError:
        pass
    return {"indexed": indexed, "live_drive": live}


@router.post("/sync-system", dependencies=[Depends(verify_api_key)])
async def sync_system(payload: dict | None = None):
    """Scan APTP/, index all project subfolders, optionally run pipeline on changes."""
    if not drive_enabled():
        raise HTTPException(400, "Google Drive is disabled. Use /intake/discover with local_path.")

    body = payload or {}
    try:
        result = await sync_system_root(
            trigger_run=body.get("trigger_run", False),
            mode=body.get("mode"),
            force_reprocess=body.get("force_reprocess", False),
            project_folder_name=body.get("project_folder_name"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return {
        "system_root_name": result.system_root_name,
        "system_root_folder_id": result.system_root_folder_id,
        "project_folders_found": result.project_folders_found,
        "new_project_folders": result.new_project_folders,
        "projects": [
            {
                "project_id": p.project_id,
                "folder_name": p.folder_name,
                "drive_folder_id": p.drive_folder_id,
                "files_discovered": p.files_discovered,
                "files_to_process": p.files_to_process,
                "changes": p.changes,
                "skipped": p.skipped,
                "pipeline_run_id": p.pipeline_run_id,
                "pipeline_status": p.pipeline_status,
                "error": p.error,
            }
            for p in result.projects
        ],
    }


@router.get("/registry", dependencies=[Depends(verify_api_key)])
async def list_registry(project_id: Optional[str] = Query(default=None)):
    registry = get_intake_registry()
    return {"roots": await registry.list_roots(project_id=project_id)}


@router.get("/registry/{root_id}", dependencies=[Depends(verify_api_key)])
async def get_registry_snapshot(root_id: str):
    registry = get_intake_registry()
    snapshot = await registry.get_registry_snapshot(root_id)
    if not snapshot:
        raise HTTPException(404, "Intake root not found")
    return snapshot


@router.get("/changes", dependencies=[Depends(verify_api_key)])
async def list_changes(
    project_id: Optional[str] = Query(default=None),
    root_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    registry = get_intake_registry()
    return {"changes": await registry.list_changes(root_id=root_id, project_id=project_id, limit=limit)}


@router.post("/discover", dependencies=[Depends(verify_api_key)])
async def discover_folder(payload: dict):
    """Preview discovery for one project folder or entire APTP system root."""
    project_id = payload.get("project_id", "demo")
    folder_id = payload.get("drive_folder_id")
    local_path = payload.get("local_path")
    force = payload.get("force_reprocess", False)
    sync_all = payload.get("sync_system_root", False)

    if sync_all or payload.get("use_system_root"):
        if not drive_enabled():
            raise HTTPException(400, "Google Drive is disabled. Use local_path instead of system root sync.")
        result = await sync_system_root(trigger_run=False, force_reprocess=force)
        return {
            "mode": "system_root",
            "system_root_folder_id": result.system_root_folder_id,
            "project_folders_found": result.project_folders_found,
            "new_project_folders": result.new_project_folders,
            "projects": [
                {
                    "project_id": p.project_id,
                    "folder_name": p.folder_name,
                    "files_discovered": p.files_discovered,
                    "files_to_process": p.files_to_process,
                    "changes": p.changes,
                }
                for p in result.projects
            ],
        }

    if local_path:
        from pathlib import Path

        sync = await sync_local_intake(project_id, Path(local_path), force_reprocess=force)
        discovery = sync.discovery
    elif folder_id:
        if not drive_enabled():
            raise HTTPException(400, "Google Drive is disabled. Use local_path instead of drive_folder_id.")
        sync = await sync_drive_intake(project_id, folder_id, force_reprocess=force)
        discovery = sync.discovery
    else:
        raise HTTPException(
            400,
            "Provide sync_system_root=true, drive_folder_id, or local_path",
        )

    return {
        "project_id": project_id,
        "root_id": sync.root_id,
        "root_folder_id": discovery.root_folder_id,
        "folders_discovered": len(discovery.folders),
        "files_discovered": len(discovery.files),
        "files_to_process": len(sync.files_to_process),
        "changes": [
            {
                "event_type": c.event_type.value,
                "entity_type": c.entity_type,
                "entity_path": c.entity_path,
            }
            for c in sync.changes
        ],
        "inferred_types": {
            f.relative_path: f.inferred_doc_type.value for f in discovery.files
        },
    }
