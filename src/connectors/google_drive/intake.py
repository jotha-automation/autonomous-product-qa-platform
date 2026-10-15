"""Fetch, discover, register, and parse documents from Drive or local trees."""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.connectors.google_drive.connector import GoogleDriveConnector, get_google_drive_connector
from src.intake.discovery import (
    DiscoveredFile,
    DiscoveryResult,
    DiscoverySource,
    discover_from_drive_entries,
    discover_local_tree,
    infer_soft_intake_role,
)
from src.intake.guardrails import validate_upload_file
from src.intake.combined import expand_combined_intake_documents
from src.intake.file_catalog import enrich_documents_with_catalog, support_files_for_ingest
from src.intake.metadata import MetadataExtractor
from src.intake.parser import DocumentParser
from src.intake.schemas.canonical import DocumentTypeEnum
from src.services.intake_registry import SyncResult, get_intake_registry

logger = logging.getLogger("google_drive_intake")
_parser = DocumentParser()
_extractor = MetadataExtractor()


async def discover_drive_folder(
    folder_id: str,
    connector: Optional[GoogleDriveConnector] = None,
    root_name: str = "",
) -> DiscoveryResult:
    drive = connector or get_google_drive_connector()
    entries = await drive.list_files_recursive(folder_id)
    return discover_from_drive_entries(entries, folder_id, root_name=root_name or folder_id)


async def discover_local_folder(
    root_path: Path,
    root_id: str = "local-root",
) -> DiscoveryResult:
    return discover_local_tree(root_path.resolve(), root_id=root_id)


async def sync_drive_intake(
    project_id: str,
    folder_id: str,
    connector: Optional[GoogleDriveConnector] = None,
    *,
    force_reprocess: bool = False,
    use_db: bool = True,
) -> SyncResult:
    """Discover folder tree, diff against Postgres registry, return files to process."""
    discovery = await discover_drive_folder(folder_id, connector=connector)
    if not use_db or not _db_enabled():
        return SyncResult(
            root_id="",
            project_id=project_id,
            discovery=discovery,
            files_to_process=list(discovery.files),
            is_initial_sync=True,
        )
    registry = get_intake_registry()
    return await registry.sync_discovery(project_id, discovery, force_reprocess=force_reprocess)


async def sync_local_intake(
    project_id: str,
    root_path: Path,
    *,
    force_reprocess: bool = False,
    use_db: bool = True,
) -> SyncResult:
    discovery = await discover_local_folder(root_path, root_id=str(root_path))
    if not use_db or not _db_enabled():
        return SyncResult(
            root_id="",
            project_id=project_id,
            discovery=discovery,
            files_to_process=list(discovery.files),
            is_initial_sync=True,
        )
    registry = get_intake_registry()
    return await registry.sync_discovery(project_id, discovery, force_reprocess=force_reprocess)


def _db_enabled() -> bool:
    return os.getenv("APTP_DISABLE_INTAKE_DB", "false").lower() not in {"1", "true", "yes"}


async def parse_discovered_files(
    discovered_files: List[DiscoveredFile],
    project_id: str,
    connector: Optional[GoogleDriveConnector] = None,
) -> List[Dict[str, Any]]:
    """Download/parse only the given discovered files."""
    drive = connector or get_google_drive_connector()
    documents: List[Dict[str, Any]] = []

    for item in discovered_files:
        try:
            if item.source == DiscoverySource.LOCAL and item.local_path:
                local_path = Path(item.local_path)
                raw = local_path.read_bytes()
                violations = validate_upload_file(item.relative_path, raw)
                if violations:
                    logger.warning("Skipping %s: %s", item.relative_path, violations[0].message)
                    continue
                doc = _parser.parse_file(local_path)
            else:
                content = await drive.download_file(item.file_id)
                if not content:
                    logger.warning("Empty content for %s", item.relative_path)
                    continue
                violations = validate_upload_file(item.relative_path, content, mime=item.mime_type or "")
                if violations:
                    logger.warning(
                        "Skipping %s: %s",
                        item.relative_path,
                        violations[0].message,
                    )
                    continue
                doc = _parser.parse_bytes(item.name, content, doc_id=item.file_id)

            if doc.document_type == DocumentTypeEnum.OTHER:
                doc.document_type = item.inferred_doc_type

            doc = _extractor.extract(doc)
            doc_dict = doc.to_dict()
            doc_dict["project_id"] = project_id
            doc_dict["drive_file_id"] = item.file_id
            doc_dict["relative_path"] = item.relative_path
            doc_dict["inferred_doc_type"] = item.inferred_doc_type.value
            doc_dict.setdefault("extra", {})
            doc_dict["extra"]["soft_intake_role"] = infer_soft_intake_role(
                item.relative_path, item.name, item.mime_type
            )
            documents.append(doc_dict)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", item.relative_path, exc)

    documents, catalog = enrich_documents_with_catalog(documents)
    if catalog.get("summary"):
        logger.info("Intake file catalog: %s", catalog["summary"])
    return documents, catalog


async def ingest_from_sync_result(
    sync: SyncResult,
    connector: Optional[GoogleDriveConnector] = None,
    *,
    prefer_mandatory: bool = True,
    run_id: Optional[str] = None,
    mark_processed: bool = False,
    intake_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Parse discovered files — always ingests full project tree (adaptive pipeline)."""
    del intake_mode  # legacy param; profile detected at runtime from documents
    to_process = list(sync.discovery.files) if sync.discovery.files else sync.files_to_process
    if prefer_mandatory and sync.discovery.files and not sync.discovery.has_combined_intake_file():
        mandatory = sync.discovery.best_mandatory_set()
        support_paths = support_files_for_ingest(sync.discovery.files)
        if mandatory and len(mandatory) < len(sync.discovery.files):
            mandatory_paths = {f.relative_path for f in mandatory}
            changed_paths = {f.relative_path for f in sync.files_to_process}
            prioritized = [
                f
                for f in sync.discovery.files
                if f.relative_path in mandatory_paths
                or f.relative_path in changed_paths
                or f.relative_path in support_paths
            ]
            if prioritized:
                to_process = prioritized

    documents, intake_catalog = await parse_discovered_files(to_process, sync.project_id, connector=connector)

    if mark_processed and sync.root_id and _db_enabled():
        registry = get_intake_registry()
        await registry.mark_files_processed(
            sync.root_id,
            sync.project_id,
            [d["relative_path"] for d in documents if d.get("relative_path")],
            run_id=run_id,
        )

    logger.info(
        "Ingest project=%s root=%s discovered=%d processed=%d docs=%d catalog=%s",
        sync.project_id,
        sync.discovery.root_folder_id,
        len(sync.discovery.files),
        len(to_process),
        len(documents),
        intake_catalog.get("summary", ""),
    )
    expanded = expand_combined_intake_documents(documents)
    for doc in expanded:
        doc.setdefault("intake_catalog", intake_catalog)
    return expanded


async def fetch_documents_from_folder(
    folder_id: str,
    project_id: str = "unknown",
    connector: Optional[GoogleDriveConnector] = None,
    *,
    force_reprocess: bool = False,
) -> List[Dict[str, Any]]:
    """Discover, register changes, and parse documents from a Drive folder tree."""
    sync = await sync_drive_intake(
        project_id,
        folder_id,
        connector=connector,
        force_reprocess=force_reprocess,
    )
    return await ingest_from_sync_result(sync, connector=connector)


async def fetch_documents_from_local_root(
    root_path: Path,
    project_id: str = "unknown",
    *,
    force_reprocess: bool = False,
) -> List[Dict[str, Any]]:
    sync = await sync_local_intake(project_id, root_path, force_reprocess=force_reprocess)
    return await ingest_from_sync_result(sync, connector=None)


async def fetch_documents_by_ids(
    file_ids: List[str],
    project_id: str = "unknown",
    connector: Optional[GoogleDriveConnector] = None,
) -> List[Dict[str, Any]]:
    """Download specific Drive file IDs (legacy path — no registry diff)."""
    drive = connector or get_google_drive_connector()
    documents: List[Dict[str, Any]] = []

    for file_id in file_ids:
        meta = await drive.get_file_metadata(file_id)
        name = meta.get("name", file_id) if meta else file_id
        rel = (meta or {}).get("relative_path", name)
        content = await drive.download_file(file_id)
        if not content:
            continue
        doc = _parser.parse_bytes(name, content, doc_id=file_id)
        doc = _extractor.extract(doc)
        doc_dict = doc.to_dict()
        doc_dict["project_id"] = project_id
        doc_dict["drive_file_id"] = file_id
        doc_dict["relative_path"] = rel
        documents.append(doc_dict)

    return documents
