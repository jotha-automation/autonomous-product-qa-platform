"""Single Drive system root (APTP/) — all client project folders live underneath."""
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.connectors.google_drive.connector import GoogleDriveConnector, get_google_drive_connector
from src.connectors.google_drive.intake import ingest_from_sync_result, sync_drive_intake
from src.services.intake_registry import ChangeEventType, IntakeChange, get_intake_registry

logger = logging.getLogger("drive_system_root")

MANIFEST_FILENAME = "manifest.json"
_DEFAULT_SYSTEM_ROOT_NAME = "APTP"


def get_system_root_folder_id() -> str:
    """APTP folder ID — sole configured Drive root for all projects."""
    root_id = (
        (os.getenv("GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID") or "").strip()
        or (os.getenv("GOOGLE_DRIVE_DEFAULT_FOLDER_ID") or "").strip()
    )
    if not root_id:
        raise ValueError(
            "GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID is not set. "
            "Create folder 'APTP' in Drive, share with service account, paste Folder ID."
        )
    return root_id


def get_system_root_name() -> str:
    return (os.getenv("GOOGLE_DRIVE_SYSTEM_ROOT_NAME") or _DEFAULT_SYSTEM_ROOT_NAME).strip()


@dataclass
class ProjectFolder:
    """Direct child folder under APTP — one client/project."""

    drive_folder_id: str
    folder_name: str
    project_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    modified_time: Optional[str] = None

    @property
    def folder_path(self) -> str:
        return f"{get_system_root_name()}/{self.folder_name}"


@dataclass
class ProjectSyncOutcome:
    project_id: str
    folder_name: str
    drive_folder_id: str
    sync_root_id: str = ""
    files_discovered: int = 0
    files_to_process: int = 0
    changes: int = 0
    pipeline_run_id: Optional[str] = None
    pipeline_status: Optional[str] = None
    pipeline_auto_skipped: bool = False
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class SystemRootSyncResult:
    system_root_folder_id: str
    system_root_name: str
    project_folders_found: int = 0
    new_project_folders: List[str] = field(default_factory=list)
    projects: List[ProjectSyncOutcome] = field(default_factory=list)


def project_id_from_folder_name(folder_name: str) -> str:
    """Derive project_id when manifest.json is absent."""
    slug = folder_name.strip()
    if slug.upper().startswith("APTP-"):
        slug = slug[5:]
    slug = re.sub(r"[^A-Za-z0-9\-]+", "-", slug).strip("-").upper()
    return slug or folder_name


async def load_folder_manifest(
    connector: GoogleDriveConnector,
    project_folder_id: str,
) -> Dict[str, Any]:
    """Read manifest.json from the project folder root if present."""
    entries = await connector.list_folder_children(project_folder_id)
    for entry in entries:
        if entry.get("name") == MANIFEST_FILENAME and entry.get("mimeType") != "application/vnd.google-apps.folder":
            raw = await connector.download_file(entry["id"])
            if raw:
                try:
                    return json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning("Invalid manifest.json in folder %s", project_folder_id)
    return {}


async def resolve_project_folder(
    connector: GoogleDriveConnector,
    folder_entry: Dict[str, Any],
) -> ProjectFolder:
    folder_id = folder_entry["id"]
    folder_name = folder_entry.get("name", folder_id)
    manifest = await load_folder_manifest(connector, folder_id)

    project_id = (manifest.get("project_id") or "").strip() or project_id_from_folder_name(folder_name)
    metadata: Dict[str, Any] = {}
    if manifest.get("target_url"):
        metadata["target_url"] = manifest["target_url"]
    if manifest.get("release"):
        metadata["release"] = manifest["release"]
    if manifest.get("client") or manifest.get("owner"):
        metadata["client"] = manifest.get("client") or manifest.get("owner")
    if manifest.get("project_name"):
        metadata["project_name"] = manifest["project_name"]
    if "auto_pipeline" in manifest:
        metadata["auto_pipeline"] = manifest["auto_pipeline"]

    return ProjectFolder(
        drive_folder_id=folder_id,
        folder_name=folder_name,
        project_id=project_id,
        metadata=metadata,
        modified_time=folder_entry.get("modifiedTime"),
    )


async def list_project_folders_under_system_root(
    connector: Optional[GoogleDriveConnector] = None,
    system_root_id: Optional[str] = None,
) -> List[ProjectFolder]:
    """List every direct child folder of APTP (each = one project)."""
    drive = connector or get_google_drive_connector()
    root_id = system_root_id or get_system_root_folder_id()
    children = await drive.list_child_folders(root_id)

    projects: List[ProjectFolder] = []
    for entry in children:
        name = entry.get("name", "")
        if name.startswith("."):
            continue
        projects.append(await resolve_project_folder(drive, entry))

    logger.info(
        "System root %s (%s): %d project folders",
        get_system_root_name(),
        root_id,
        len(projects),
    )
    return projects


async def should_auto_trigger_pipeline(
    registry: Any,
    drive_folder_id: str,
    manifest: Dict[str, Any],
) -> bool:
    """Whether Drive sync may start a pipeline without operator action.

    New draft projects stay indexed only until the operator clicks
    "Ejecutar pipeline QA". Set manifest.json ``auto_pipeline: true`` to
    opt a project back into watcher-driven first runs.
    """
    if manifest.get("auto_pipeline") is True:
        return True
    if manifest.get("auto_pipeline") is False:
        return False
    state = await registry.get_processing_state(drive_folder_id)
    return bool(state.get("processed")) or (state.get("process_count") or 0) > 0


async def sync_system_root(
    *,
    connector: Optional[GoogleDriveConnector] = None,
    trigger_run: bool = False,
    mode: Optional[str] = None,
    force_reprocess: bool = False,
    project_folder_name: Optional[str] = None,
) -> SystemRootSyncResult:
    """Index all project folders under APTP and sync/process those with changes."""
    drive = connector or get_google_drive_connector()
    root_id = get_system_root_folder_id()
    root_name = get_system_root_name()
    run_mode = mode or os.getenv("APTP_DRIVE_DEFAULT_MODE", "full")
    registry = get_intake_registry()

    await registry.ensure_system_root(root_id, root_name)

    all_projects = await list_project_folders_under_system_root(drive, root_id)
    if project_folder_name:
        all_projects = [p for p in all_projects if p.folder_name == project_folder_name]

    known_ids = await registry.list_project_folder_ids(root_id)
    current_ids = {p.drive_folder_id for p in all_projects}
    new_ids = current_ids - known_ids

    result = SystemRootSyncResult(
        system_root_folder_id=root_id,
        system_root_name=root_name,
        project_folders_found=len(all_projects),
        new_project_folders=[p.folder_name for p in all_projects if p.drive_folder_id in new_ids],
    )

    for project in all_projects:
        outcome = ProjectSyncOutcome(
            project_id=project.project_id,
            folder_name=project.folder_name,
            drive_folder_id=project.drive_folder_id,
        )
        try:
            sync = await sync_drive_intake(
                project.project_id,
                project.drive_folder_id,
                connector=drive,
                force_reprocess=force_reprocess,
            )
            await registry.link_project_to_system_root(
                sync.root_id,
                system_root_drive_id=root_id,
                folder_slug=project.folder_name,
            )

            if project.drive_folder_id in new_ids:
                await registry.log_project_event(
                    sync.root_id,
                    project.project_id,
                    IntakeChange(
                        event_type=ChangeEventType.PROJECT_FOLDER_CREATED,
                        entity_type="project_folder",
                        entity_path=project.folder_path,
                        entity_id=project.drive_folder_id,
                        details={
                            "project_id": project.project_id,
                            "folder_name": project.folder_name,
                        },
                    ),
                )

            outcome.sync_root_id = sync.root_id
            outcome.files_discovered = len(sync.discovery.files)
            outcome.files_to_process = len(sync.files_to_process)
            outcome.changes = len(sync.changes)

            if not sync.files_to_process:
                outcome.skipped = True
                result.projects.append(outcome)
                continue

            if trigger_run:
                if not await should_auto_trigger_pipeline(
                    registry, project.drive_folder_id, project.metadata
                ):
                    outcome.pipeline_auto_skipped = True
                    logger.info(
                        "Drive sync indexed %s (%s); pipeline auto-start skipped "
                        "(draft project — operator must start first run)",
                        project.folder_name,
                        project.project_id,
                    )
                    result.projects.append(outcome)
                    continue

                # Business gates: register the project and enforce reprocessing
                # limits / closed-project rules before any watcher-initiated run,
                # mirroring drive_manager._run_pipeline_async. A project at its
                # limit or closed is skipped gracefully (outcome recorded), never
                # crashing the sync loop.
                await registry.ensure_drive_project_root(
                    project.drive_folder_id, project.project_id, project.folder_name
                )
                try:
                    await registry.reserve_pipeline_run(project.drive_folder_id)
                except ValueError as gate_exc:
                    outcome.pipeline_auto_skipped = True
                    outcome.pipeline_status = "skipped"
                    logger.info(
                        "Drive sync indexed %s (%s); pipeline auto-start skipped (%s)",
                        project.folder_name,
                        project.project_id,
                        gate_exc,
                    )
                    result.projects.append(outcome)
                    continue

                from src.orchestrator.engine import ProductQAOrchestrator

                documents = await ingest_from_sync_result(sync, connector=drive)
                if documents:
                    run_id = f"run-{uuid.uuid4().hex[:12]}"
                    outcome.pipeline_run_id = run_id
                    await registry.record_pipeline_run_started(
                        project.drive_folder_id, run_id, project.project_id
                    )
                    pipeline_status = "failed"
                    try:
                        run_result = await ProductQAOrchestrator().run(
                            {
                                "run_id": run_id,
                                "project_id": project.project_id,
                                "mode": run_mode,
                                "documents": documents,
                                "metadata": project.metadata,
                                "drive_folder_id": project.drive_folder_id,
                            }
                        )
                        outcome.pipeline_run_id = run_result.get("run_id") or run_id
                        pipeline_status = run_result.get("status") or "failed"
                        outcome.pipeline_status = pipeline_status
                        if sync.root_id and pipeline_status not in ("awaiting_input", "cancelled"):
                            await registry.mark_files_processed(
                                sync.root_id,
                                project.project_id,
                                [d.get("relative_path", "") for d in documents if d.get("relative_path")],
                                run_id=outcome.pipeline_run_id,
                            )
                    finally:
                        try:
                            await registry.record_pipeline_finished(
                                project.drive_folder_id,
                                outcome.pipeline_run_id or run_id,
                                pipeline_status,
                            )
                        except Exception as reg_exc:
                            logger.warning(
                                "Failed to record pipeline status for %s: %s",
                                project.folder_name,
                                reg_exc,
                            )
        except Exception as exc:
            outcome.error = str(exc)
            logger.error("System root sync failed for %s: %s", project.folder_name, exc)

        result.projects.append(outcome)

    await registry.touch_system_root(root_id, project_count=len(all_projects))
    return result
