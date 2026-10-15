"""Postgres-backed intake registry: folder index, file snapshots, change log."""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.intake.discovery import DiscoveredFile, DiscoveryResult, DiscoverySource
from src.services.project_limits import MAX_PROCESS_COUNT
from src.services.db_models import (
    Base,
    IntakeChangeLog,
    IntakeFileRecord,
    IntakeRootFolder,
    IntakeSubfolder,
    IntakeSystemRoot,
    PipelineRunRecord,
)
from src.services.postgres import get_session_factory
from src.services.run_history import count_publishable_artifact_files, has_published_reports_file

logger = logging.getLogger("intake_registry")

_INTAKE_CHILD_TABLES = (IntakeSubfolder, IntakeFileRecord, IntakeChangeLog)


def _aware_dt(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ChangeEventType(str, Enum):
    ROOT_REGISTERED = "root_registered"
    PROJECT_FOLDER_CREATED = "project_folder_created"
    FOLDER_CREATED = "folder_created"
    FOLDER_SEEN = "folder_seen"
    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"
    FILE_DELETED = "file_deleted"
    FILE_PROCESSED = "file_processed"
    SYNC_COMPLETED = "sync_completed"


@dataclass
class IntakeChange:
    event_type: ChangeEventType
    entity_type: str
    entity_path: str
    entity_id: Optional[str] = None
    old_fingerprint: Optional[str] = None
    new_fingerprint: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncResult:
    root_id: str
    project_id: str
    discovery: DiscoveryResult
    changes: List[IntakeChange] = field(default_factory=list)
    files_to_process: List[DiscoveredFile] = field(default_factory=list)
    is_initial_sync: bool = False


def compute_file_changes(
    previous: Dict[str, IntakeFileRecord],
    current_files: List[DiscoveredFile],
) -> tuple[List[IntakeChange], List[DiscoveredFile]]:
    """Diff current discovery against DB snapshots. Returns changes + files needing processing."""
    changes: List[IntakeChange] = []
    to_process: List[DiscoveredFile] = []
    current_by_path = {f.relative_path: f for f in current_files}

    for rel_path, discovered in current_by_path.items():
        prev = previous.get(rel_path)
        if prev is None:
            changes.append(
                IntakeChange(
                    event_type=ChangeEventType.FILE_CREATED,
                    entity_type="file",
                    entity_path=rel_path,
                    entity_id=discovered.file_id,
                    new_fingerprint=discovered.fingerprint,
                    details={"inferred_doc_type": discovered.inferred_doc_type.value},
                )
            )
            to_process.append(discovered)
        elif prev.content_fingerprint != discovered.fingerprint:
            changes.append(
                IntakeChange(
                    event_type=ChangeEventType.FILE_MODIFIED,
                    entity_type="file",
                    entity_path=rel_path,
                    entity_id=discovered.file_id,
                    old_fingerprint=prev.content_fingerprint,
                    new_fingerprint=discovered.fingerprint,
                    details={"inferred_doc_type": discovered.inferred_doc_type.value},
                )
            )
            to_process.append(discovered)

    for rel_path, prev in previous.items():
        if rel_path not in current_by_path:
            changes.append(
                IntakeChange(
                    event_type=ChangeEventType.FILE_DELETED,
                    entity_type="file",
                    entity_path=rel_path,
                    entity_id=prev.drive_file_id,
                    old_fingerprint=prev.content_fingerprint,
                )
            )

    return changes, to_process


def compute_folder_changes(
    previous_paths: set[str],
    current_paths: set[str],
) -> List[IntakeChange]:
    changes: List[IntakeChange] = []
    for path in sorted(current_paths - previous_paths):
        changes.append(
            IntakeChange(
                event_type=ChangeEventType.FOLDER_CREATED,
                entity_type="folder",
                entity_path=path,
            )
        )
    for path in sorted(current_paths & previous_paths):
        if path not in previous_paths:
            continue
        # Re-seen folders on every sync could be noisy; only log new folders above
    return changes


class IntakeRegistryService:
    @staticmethod
    def _canonical_intake_root(rows: List[IntakeRootFolder]) -> IntakeRootFolder:
        return max(
            rows,
            key=lambda row: (
                row.process_count or 0,
                1 if row.processed else 0,
                _aware_dt(row.last_synced_at),
                _aware_dt(row.first_seen_at),
            ),
        )

    @staticmethod
    def _merge_root_metadata(canonical: IntakeRootFolder, duplicate: IntakeRootFolder) -> None:
        canonical.process_count = max(canonical.process_count or 0, duplicate.process_count or 0)
        canonical.processed = bool(canonical.processed or duplicate.processed)
        canonical.last_file_count = max(canonical.last_file_count or 0, duplicate.last_file_count or 0)
        canonical.last_folder_count = max(canonical.last_folder_count or 0, duplicate.last_folder_count or 0)
        if duplicate.last_synced_at and (
            not canonical.last_synced_at or duplicate.last_synced_at > canonical.last_synced_at
        ):
            canonical.last_synced_at = duplicate.last_synced_at
        if (duplicate.process_count or 0) >= (canonical.process_count or 0):
            for attr in (
                "project_id",
                "root_name",
                "last_pipeline_run_id",
                "last_pipeline_status",
                "last_successful_run_id",
                "last_results_drive_folder_id",
                "project_closed_at",
            ):
                dup_val = getattr(duplicate, attr, None)
                if dup_val is not None:
                    setattr(canonical, attr, dup_val)

    async def _merge_intake_root_duplicates(
        self,
        session: AsyncSession,
        rows: List[IntakeRootFolder],
    ) -> IntakeRootFolder:
        canonical = self._canonical_intake_root(rows)
        for duplicate in rows:
            if duplicate.id == canonical.id:
                continue
            self._merge_root_metadata(canonical, duplicate)
            for child_table in _INTAKE_CHILD_TABLES:
                await session.execute(
                    update(child_table)
                    .where(child_table.root_id == duplicate.id)
                    .values(root_id=canonical.id)
                )
            await session.delete(duplicate)
        await session.flush()
        return canonical

    async def _get_intake_root_by_drive_folder(
        self,
        session: AsyncSession,
        drive_folder_id: str,
        *,
        for_update: bool = False,
    ) -> Optional[IntakeRootFolder]:
        stmt = select(IntakeRootFolder).where(IntakeRootFolder.drive_folder_id == drive_folder_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        if not rows:
            return None
        if len(rows) == 1:
            return rows[0]
        logger.warning(
            "Duplicate intake_root_folders for drive_folder_id=%s (%s rows); coalescing",
            drive_folder_id,
            len(rows),
        )
        return await self._merge_intake_root_duplicates(session, rows)

    async def sync_discovery(
        self,
        project_id: str,
        discovery: DiscoveryResult,
        *,
        force_reprocess: bool = False,
    ) -> SyncResult:
        factory = get_session_factory()
        async with factory() as session:
            return await self._sync_discovery(session, project_id, discovery, force_reprocess=force_reprocess)

    async def _sync_discovery(
        self,
        session: AsyncSession,
        project_id: str,
        discovery: DiscoveryResult,
        *,
        force_reprocess: bool = False,
    ) -> SyncResult:
        root = await self._get_or_create_root(session, project_id, discovery)
        is_initial = root.last_synced_at is None

        prev_folders = await self._load_subfolder_paths(session, root.id)
        folder_changes = compute_folder_changes(prev_folders, set(discovery.folder_paths))
        await self._upsert_subfolders(session, root.id, discovery)

        prev_files = await self._load_file_records(session, root.id)
        file_changes, to_process = compute_file_changes(prev_files, discovery.files)
        if force_reprocess:
            to_process = list(discovery.files)

        all_changes = folder_changes + file_changes
        if is_initial:
            all_changes.insert(
                0,
                IntakeChange(
                    event_type=ChangeEventType.ROOT_REGISTERED,
                    entity_type="root",
                    entity_path=discovery.root_name,
                    entity_id=discovery.root_folder_id,
                    details={"file_count": len(discovery.files), "folder_count": len(discovery.folders)},
                ),
            )

        await self._persist_file_snapshots(session, root.id, discovery.files, to_process)
        await self._write_change_logs(session, root.id, project_id, all_changes)

        root.last_synced_at = datetime.utcnow()
        root.last_file_count = len(discovery.files)
        root.last_folder_count = len(discovery.folders)
        await session.commit()

        if to_process or is_initial:
            all_changes.append(
                IntakeChange(
                    event_type=ChangeEventType.SYNC_COMPLETED,
                    entity_type="root",
                    entity_path=discovery.root_name,
                    details={
                        "files_discovered": len(discovery.files),
                        "files_to_process": len(to_process),
                        "changes": len(all_changes),
                    },
                )
            )

        logger.info(
            "Intake sync project=%s root=%s files=%d changes=%d to_process=%d",
            project_id,
            discovery.root_folder_id,
            len(discovery.files),
            len(all_changes),
            len(to_process),
        )

        return SyncResult(
            root_id=root.id,
            project_id=project_id,
            discovery=discovery,
            changes=all_changes,
            files_to_process=to_process,
            is_initial_sync=is_initial,
        )

    async def mark_files_processed(
        self,
        root_id: str,
        project_id: str,
        relative_paths: List[str],
        run_id: Optional[str] = None,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            now = datetime.utcnow()
            for rel in relative_paths:
                result = await session.execute(
                    select(IntakeFileRecord).where(
                        IntakeFileRecord.root_id == root_id,
                        IntakeFileRecord.relative_path == rel,
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    record.last_processed_at = now
                    record.status = "processed"
                    record.last_run_id = run_id
                    await self._log_change(
                        session,
                        root_id,
                        project_id,
                        IntakeChange(
                            event_type=ChangeEventType.FILE_PROCESSED,
                            entity_type="file",
                            entity_path=rel,
                            entity_id=record.drive_file_id,
                            details={"run_id": run_id},
                        ),
                    )
            await session.commit()

    async def ensure_system_root(self, drive_folder_id: str, root_name: str) -> str:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(IntakeSystemRoot).where(IntakeSystemRoot.drive_folder_id == drive_folder_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = IntakeSystemRoot(
                    id=str(uuid.uuid4()),
                    drive_folder_id=drive_folder_id,
                    root_name=root_name,
                )
                session.add(row)
                await session.flush()
            else:
                row.root_name = root_name
            await session.commit()
            return row.id

    async def touch_system_root(self, drive_folder_id: str, project_count: int = 0) -> None:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(IntakeSystemRoot).where(IntakeSystemRoot.drive_folder_id == drive_folder_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.last_synced_at = datetime.utcnow()
                row.project_folder_count = project_count
                await session.commit()

    async def list_project_folder_ids(self, system_root_drive_id: str) -> set[str]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(IntakeRootFolder.drive_folder_id).where(
                    IntakeRootFolder.system_root_drive_id == system_root_drive_id
                )
            )
            return set(result.scalars().all())

    async def link_project_to_system_root(
        self,
        root_id: str,
        *,
        system_root_drive_id: str,
        folder_slug: str,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            row = await session.get(IntakeRootFolder, root_id)
            if row:
                row.system_root_drive_id = system_root_drive_id
                row.folder_slug = folder_slug
                await session.commit()

    async def log_project_event(
        self,
        root_id: str,
        project_id: str,
        change: IntakeChange,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            await self._log_change(session, root_id, project_id, change)
            await session.commit()

    async def log_system_event(
        self,
        system_root_drive_id: str,
        change: IntakeChange,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(IntakeSystemRoot).where(IntakeSystemRoot.drive_folder_id == system_root_drive_id)
            )
            sys_row = result.scalar_one_or_none()
            sys_id = sys_row.id if sys_row else system_root_drive_id
            await self._log_change(session, sys_id, "system", change)
            await session.commit()

    async def get_system_root(self) -> Optional[Dict[str, Any]]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(select(IntakeSystemRoot).limit(1))
            row = result.scalar_one_or_none()
            if not row:
                return None
            return {
                "id": row.id,
                "drive_folder_id": row.drive_folder_id,
                "root_name": row.root_name,
                "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
                "project_folder_count": row.project_folder_count,
            }

    async def list_project_folders(self, system_root_drive_id: Optional[str] = None) -> List[Dict[str, Any]]:
        factory = get_session_factory()
        async with factory() as session:
            q = select(IntakeRootFolder).where(IntakeRootFolder.system_root_drive_id.isnot(None))
            if system_root_drive_id:
                q = q.where(IntakeRootFolder.system_root_drive_id == system_root_drive_id)
            result = await session.execute(q.order_by(IntakeRootFolder.folder_slug))
            return [
                {
                    **self._root_to_dict(r),
                    "folder_slug": r.folder_slug,
                    "system_root_drive_id": r.system_root_drive_id,
                }
                for r in result.scalars().all()
            ]

    async def list_roots(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        factory = get_session_factory()
        async with factory() as session:
            q = select(IntakeRootFolder)
            if project_id:
                q = q.where(IntakeRootFolder.project_id == project_id)
            result = await session.execute(q.order_by(IntakeRootFolder.first_seen_at.desc()))
            roots = result.scalars().all()
            return [self._root_to_dict(r) for r in roots]

    async def list_changes(
        self,
        root_id: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        factory = get_session_factory()
        async with factory() as session:
            q = select(IntakeChangeLog).order_by(IntakeChangeLog.created_at.desc()).limit(limit)
            if root_id:
                q = q.where(IntakeChangeLog.root_id == root_id)
            elif project_id:
                q = q.where(IntakeChangeLog.project_id == project_id)
            result = await session.execute(q)
            return [self._change_to_dict(c) for c in result.scalars().all()]

    async def get_registry_snapshot(self, root_id: str) -> Dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            root = await session.get(IntakeRootFolder, root_id)
            if not root:
                return {}
            folders = (
                await session.execute(
                    select(IntakeSubfolder).where(IntakeSubfolder.root_id == root_id)
                )
            ).scalars().all()
            files = (
                await session.execute(
                    select(IntakeFileRecord).where(IntakeFileRecord.root_id == root_id)
                )
            ).scalars().all()
            return {
                "root": self._root_to_dict(root),
                "folders": [
                    {
                        "relative_path": f.relative_path,
                        "folder_name": f.folder_name,
                        "first_seen_at": f.first_seen_at.isoformat() if f.first_seen_at else None,
                        "last_seen_at": f.last_seen_at.isoformat() if f.last_seen_at else None,
                    }
                    for f in folders
                ],
                "files": [
                    {
                        "relative_path": fr.relative_path,
                        "file_name": fr.file_name,
                        "inferred_doc_type": fr.inferred_doc_type,
                        "status": fr.status,
                        "content_fingerprint": fr.content_fingerprint,
                        "last_processed_at": fr.last_processed_at.isoformat() if fr.last_processed_at else None,
                    }
                    for fr in files
                ],
            }

    async def _get_or_create_root(
        self, session: AsyncSession, project_id: str, discovery: DiscoveryResult
    ) -> IntakeRootFolder:
        if discovery.root_folder_id:
            existing = await self._get_intake_root_by_drive_folder(session, discovery.root_folder_id)
            if existing is not None:
                if existing.project_id != project_id:
                    existing.project_id = project_id
                if discovery.root_name:
                    existing.root_name = discovery.root_name
                existing.source = discovery.source.value
                return existing

        result = await session.execute(
            select(IntakeRootFolder).where(
                IntakeRootFolder.project_id == project_id,
                IntakeRootFolder.drive_folder_id == discovery.root_folder_id,
            )
        )
        root = result.scalar_one_or_none()
        now = datetime.utcnow()
        if root is None:
            root = IntakeRootFolder(
                id=str(uuid.uuid4()),
                project_id=project_id,
                drive_folder_id=discovery.root_folder_id,
                root_name=discovery.root_name,
                source=discovery.source.value,
                first_seen_at=now,
            )
            session.add(root)
            await session.flush()
        return root

    async def _load_subfolder_paths(self, session: AsyncSession, root_id: str) -> set[str]:
        result = await session.execute(
            select(IntakeSubfolder.relative_path).where(IntakeSubfolder.root_id == root_id)
        )
        return set(result.scalars().all())

    async def _upsert_subfolders(
        self, session: AsyncSession, root_id: str, discovery: DiscoveryResult
    ) -> None:
        now = datetime.utcnow()
        for folder in discovery.folders:
            if not folder.relative_path:
                continue
            result = await session.execute(
                select(IntakeSubfolder).where(
                    IntakeSubfolder.root_id == root_id,
                    IntakeSubfolder.relative_path == folder.relative_path,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                session.add(
                    IntakeSubfolder(
                        id=str(uuid.uuid4()),
                        root_id=root_id,
                        relative_path=folder.relative_path,
                        folder_name=folder.name,
                        drive_folder_id=folder.drive_folder_id,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
            else:
                row.last_seen_at = now
                if folder.drive_folder_id:
                    row.drive_folder_id = folder.drive_folder_id

    async def _load_file_records(
        self, session: AsyncSession, root_id: str
    ) -> Dict[str, IntakeFileRecord]:
        result = await session.execute(
            select(IntakeFileRecord).where(IntakeFileRecord.root_id == root_id)
        )
        return {r.relative_path: r for r in result.scalars().all()}

    async def _persist_file_snapshots(
        self,
        session: AsyncSession,
        root_id: str,
        all_files: List[DiscoveredFile],
        to_process: List[DiscoveredFile],
    ) -> None:
        process_paths = {f.relative_path for f in to_process}
        now = datetime.utcnow()
        current_paths = {f.relative_path for f in all_files}

        existing = await self._load_file_records(session, root_id)
        for rel, record in list(existing.items()):
            if rel not in current_paths:
                record.status = "deleted"
                record.last_seen_at = now

        for discovered in all_files:
            result = await session.execute(
                select(IntakeFileRecord).where(
                    IntakeFileRecord.root_id == root_id,
                    IntakeFileRecord.relative_path == discovered.relative_path,
                )
            )
            row = result.scalar_one_or_none()
            status = "pending" if discovered.relative_path in process_paths else (row.status if row else "indexed")
            if row is None:
                session.add(
                    IntakeFileRecord(
                        id=str(uuid.uuid4()),
                        root_id=root_id,
                        drive_file_id=discovered.file_id,
                        relative_path=discovered.relative_path,
                        file_name=discovered.name,
                        mime_type=discovered.mime_type,
                        inferred_doc_type=discovered.inferred_doc_type.value,
                        content_fingerprint=discovered.fingerprint,
                        modified_at=discovered.modified_at,
                        status=status,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
            else:
                row.drive_file_id = discovered.file_id
                row.file_name = discovered.name
                row.mime_type = discovered.mime_type
                row.inferred_doc_type = discovered.inferred_doc_type.value
                row.content_fingerprint = discovered.fingerprint
                row.modified_at = discovered.modified_at
                row.last_seen_at = now
                if discovered.relative_path in process_paths:
                    row.status = "pending"

    async def _write_change_logs(
        self,
        session: AsyncSession,
        root_id: str,
        project_id: str,
        changes: List[IntakeChange],
    ) -> None:
        for change in changes:
            await self._log_change(session, root_id, project_id, change)

    async def _log_change(
        self,
        session: AsyncSession,
        root_id: str,
        project_id: str,
        change: IntakeChange,
    ) -> None:
        session.add(
            IntakeChangeLog(
                id=str(uuid.uuid4()),
                root_id=root_id,
                project_id=project_id,
                event_type=change.event_type.value,
                entity_type=change.entity_type,
                entity_path=change.entity_path,
                entity_id=change.entity_id,
                old_fingerprint=change.old_fingerprint,
                new_fingerprint=change.new_fingerprint,
                details=change.details,
            )
        )

    @staticmethod
    def processing_state_from_row(row: Optional[IntakeRootFolder]) -> Dict[str, Any]:
        count = row.process_count if row else 0
        closed = bool(row and row.project_closed_at)
        at_limit = count >= MAX_PROCESS_COUNT
        last_status = row.last_pipeline_status if row else None
        return {
            "processed": bool(row and row.processed),
            "process_count": count,
            "max_process_count": MAX_PROCESS_COUNT,
            "remaining_runs": max(0, MAX_PROCESS_COUNT - count),
            "can_reprocess": not closed and not at_limit,
            "project_closed": closed,
            "project_closed_at": row.project_closed_at.isoformat() if row and row.project_closed_at else None,
            "last_pipeline_run_id": row.last_pipeline_run_id if row else None,
            "last_pipeline_status": last_status,
            "pipeline_succeeded": last_status == "completed",
            "last_successful_run_id": row.last_successful_run_id if row else None,
            "last_results_drive_folder_id": row.last_results_drive_folder_id if row else None,
        }

    async def ensure_drive_project_root(
        self,
        drive_folder_id: str,
        project_id: str,
        folder_name: str,
    ) -> IntakeRootFolder:
        """Ensure intake_root_folders row exists for a Drive project."""
        factory = get_session_factory()
        async with factory() as session:
            row = await self._get_intake_root_by_drive_folder(session, drive_folder_id)
            if row is None:
                row = IntakeRootFolder(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    drive_folder_id=drive_folder_id,
                    root_name=folder_name,
                    source="drive",
                )
                session.add(row)
            else:
                if row.project_id != project_id:
                    row.project_id = project_id
                if folder_name:
                    row.root_name = folder_name
            await session.commit()
            await session.refresh(row)
            return row

    async def get_processing_state(self, drive_folder_id: str) -> Dict[str, Any]:
        factory = get_session_factory()
        async with factory() as session:
            row = await self._get_intake_root_by_drive_folder(session, drive_folder_id)
            await session.commit()
            return self.processing_state_from_row(row)

    async def record_pipeline_run_started(
        self,
        drive_folder_id: str,
        run_id: str,
        project_id: str,
    ) -> None:
        factory = get_session_factory()
        async with factory() as session:
            existing = await session.execute(
                select(PipelineRunRecord).where(PipelineRunRecord.run_id == run_id)
            )
            if existing.scalar_one_or_none():
                return
            session.add(
                PipelineRunRecord(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    project_id=project_id,
                    drive_folder_id=drive_folder_id,
                    status="running",
                )
            )
            await session.commit()

    async def mark_pipeline_resumed(self, drive_folder_id: str, run_id: str) -> None:
        """Mark an awaiting-input run as running again (does not consume another process slot)."""
        factory = get_session_factory()
        async with factory() as session:
            row = await self._get_intake_root_by_drive_folder(session, drive_folder_id)
            if row:
                row.last_pipeline_status = "running"
                row.last_pipeline_run_id = run_id
            run_record = await session.execute(
                select(PipelineRunRecord).where(PipelineRunRecord.run_id == run_id)
            )
            record = run_record.scalar_one_or_none()
            if record:
                record.status = "running"
                record.completed_at = None
            await session.commit()

    async def list_pipeline_runs(self, drive_folder_id: str) -> List[Dict[str, Any]]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(PipelineRunRecord)
                .where(PipelineRunRecord.drive_folder_id == drive_folder_id)
                .order_by(PipelineRunRecord.started_at.desc())
            )
            rows = result.scalars().all()
            return [self._pipeline_run_to_dict(row) for row in rows]

    async def get_run_project_id(self, run_id: str) -> Optional[str]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(PipelineRunRecord.project_id).where(PipelineRunRecord.run_id == run_id)
            )
            project_id = result.scalar_one_or_none()
            return str(project_id) if project_id else None

    async def get_run_drive_folder_id(self, run_id: str) -> Optional[str]:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(PipelineRunRecord.drive_folder_id).where(PipelineRunRecord.run_id == run_id)
            )
            drive_folder_id = result.scalar_one_or_none()
            return str(drive_folder_id) if drive_folder_id else None

    @staticmethod
    def _pipeline_run_to_dict(row: PipelineRunRecord) -> Dict[str, Any]:
        file_count = 0
        has_reports = False
        has_scripts = False
        has_evidence = False
        if row.artifact_dir:
            path = Path(row.artifact_dir)
            if path.is_dir():
                file_count = count_publishable_artifact_files(path)
                has_reports = has_published_reports_file(path / "reports.json")
                has_scripts = (path / "scripts").is_dir()
                has_evidence = (path / "evidence").is_dir() or (path / "execution-report.pdf").is_file()
        return {
            "run_id": row.run_id,
            "status": row.status,
            "source": "drive" if row.drive_uploaded else "platform",
            "drive_folder_id": row.drive_run_folder_id,
            "artifact_dir": row.artifact_dir,
            "file_count": file_count,
            "has_reports": has_reports,
            "has_scripts": has_scripts,
            "has_evidence": has_evidence,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "manifest_file_id": None,
            "files": [],
        }

    @staticmethod
    def _assert_state_can_process(state: Dict[str, Any]) -> None:
        """Raise a ValueError when the project's gate state forbids another run."""
        if state.get("project_closed"):
            raise ValueError(
                "This project is closed and delivered to the client. "
                "No further pipeline runs are allowed."
            )
        if state["process_count"] >= MAX_PROCESS_COUNT:
            raise ValueError(
                f"Maximum processing limit reached ({MAX_PROCESS_COUNT} runs per project). "
                "No further re-processing is allowed."
            )

    async def assert_can_process(self, drive_folder_id: str) -> None:
        state = await self.get_processing_state(drive_folder_id)
        self._assert_state_can_process(state)

    async def reserve_pipeline_run(self, drive_folder_id: str) -> Dict[str, Any]:
        """Verify the project may start a run and mark it as running (does not consume a success slot).

        The gate check and status update run inside a single transaction with a
        row-level lock (``SELECT ... FOR UPDATE``) so concurrent reservations
        cannot both pass the limit/closed checks and race past MAX_PROCESS_COUNT.
        """
        factory = get_session_factory()
        async with factory() as session:
            row = await self._get_intake_root_by_drive_folder(session, drive_folder_id, for_update=True)
            if not row:
                raise ValueError("Project is not registered for intake tracking")
            self._assert_state_can_process(self.processing_state_from_row(row))
            row.last_pipeline_status = "running"
            await session.commit()
            await session.refresh(row)
            return self.processing_state_from_row(row)

    async def record_pipeline_finished(
        self,
        drive_folder_id: str,
        run_id: str,
        status: str,
        *,
        results_drive_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist final pipeline outcome. Only successful completions increment process_count."""
        factory = get_session_factory()
        async with factory() as session:
            # Lock the row for the whole transaction so a concurrent completion
            # cannot read a stale process_count and push it past the limit.
            row = await self._get_intake_root_by_drive_folder(session, drive_folder_id, for_update=True)
            if not row:
                logger.warning("No intake root for drive folder %s — skipping pipeline status update", drive_folder_id)
                return self.processing_state_from_row(None)
            row.last_pipeline_run_id = run_id
            row.last_pipeline_status = status
            if status == "completed":
                current = row.process_count or 0
                # Cap at MAX_PROCESS_COUNT: even if more runs completed than
                # reservations allowed (e.g. a race), the count never exceeds the limit.
                if current < MAX_PROCESS_COUNT:
                    row.process_count = current + 1
                row.processed = True
                row.last_successful_run_id = run_id
            elif status == "partial":
                # A partial run completed end-to-end and produced deliverables
                # (tests failed at runtime). It HAS been processed, so reflect that
                # in the UI — but do not consume a success slot or mark it succeeded.
                row.processed = True
            if results_drive_folder_id:
                row.last_results_drive_folder_id = results_drive_folder_id

            run_record = await session.execute(
                select(PipelineRunRecord).where(PipelineRunRecord.run_id == run_id)
            )
            record = run_record.scalar_one_or_none()
            artifact_dir = None
            if record and record.project_id:
                candidate = (
                    Path(__file__).resolve().parents[2]
                    / "generated-test-projects"
                    / record.project_id
                    / "aptp-artifacts"
                    / run_id
                )
                if candidate.is_dir():
                    artifact_dir = str(candidate)
            if record:
                record.status = status
                record.completed_at = datetime.utcnow()
                if artifact_dir:
                    record.artifact_dir = artifact_dir
                if results_drive_folder_id:
                    record.drive_uploaded = True
                    record.drive_run_folder_id = results_drive_folder_id
            else:
                session.add(
                    PipelineRunRecord(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        project_id=row.project_id,
                        drive_folder_id=drive_folder_id,
                        status=status,
                        completed_at=datetime.utcnow(),
                        artifact_dir=artifact_dir,
                        drive_uploaded=bool(results_drive_folder_id),
                        drive_run_folder_id=results_drive_folder_id,
                    )
                )

            await session.commit()
            return self.processing_state_from_row(row)

    async def record_pipeline_completed(
        self,
        drive_folder_id: str,
        run_id: str,
        *,
        results_drive_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Backward-compatible wrapper for successful runs."""
        return await self.record_pipeline_finished(
            drive_folder_id,
            run_id,
            "completed",
            results_drive_folder_id=results_drive_folder_id,
        )

    async def close_project(self, drive_folder_id: str) -> Dict[str, Any]:
        """Mark project as definitively closed (delivered to client)."""
        factory = get_session_factory()
        async with factory() as session:
            row = await self._get_intake_root_by_drive_folder(session, drive_folder_id)
            if not row:
                raise ValueError("Project is not registered for intake tracking")
            if row.project_closed_at:
                return self.processing_state_from_row(row)
            row.project_closed_at = datetime.utcnow()
            await session.commit()
            return self.processing_state_from_row(row)

    @staticmethod
    def _root_to_dict(root: IntakeRootFolder) -> Dict[str, Any]:
        base = {
            "id": root.id,
            "project_id": root.project_id,
            "drive_folder_id": root.drive_folder_id,
            "root_name": root.root_name,
            "source": root.source,
            "first_seen_at": root.first_seen_at.isoformat() if root.first_seen_at else None,
            "last_synced_at": root.last_synced_at.isoformat() if root.last_synced_at else None,
            "last_file_count": root.last_file_count,
            "last_folder_count": root.last_folder_count,
        }
        base.update(IntakeRegistryService.processing_state_from_row(root))
        return base

    @staticmethod
    def _change_to_dict(change: IntakeChangeLog) -> Dict[str, Any]:
        return {
            "id": change.id,
            "root_id": change.root_id,
            "project_id": change.project_id,
            "event_type": change.event_type,
            "entity_type": change.entity_type,
            "entity_path": change.entity_path,
            "entity_id": change.entity_id,
            "old_fingerprint": change.old_fingerprint,
            "new_fingerprint": change.new_fingerprint,
            "details": change.details,
            "created_at": change.created_at.isoformat() if change.created_at else None,
        }


_registry: Optional[IntakeRegistryService] = None


def get_intake_registry() -> IntakeRegistryService:
    global _registry
    if _registry is None:
        _registry = IntakeRegistryService()
    return _registry
