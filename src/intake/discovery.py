"""Auto-discovery and document-type identification from folder scaffolding."""
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.intake.file_types import SKIP_EXTENSIONS, guess_mime_from_filename, is_intake_file
from src.intake.schemas.canonical import DocumentTypeEnum

# Folder segments (portfolio-webapp style: 01-Business-Requirements, intake/, etc.)
_FOLDER_TYPE_PATTERNS: Sequence[tuple[re.Pattern[str], DocumentTypeEnum]] = (
    (re.compile(r"business[-_\s]?requirements|^01[-_]", re.I), DocumentTypeEnum.BRD),
    (re.compile(r"product[-_\s]?requirements|^02[-_]", re.I), DocumentTypeEnum.PRD),
    (re.compile(r"use[-_\s]?cases?|^04[-_]", re.I), DocumentTypeEnum.UC),
    (re.compile(r"acceptance[-_\s]?criteria|^05[-_]", re.I), DocumentTypeEnum.AC),
    (re.compile(r"wireframe|ux[-_\s]?wire|^07[-_]", re.I), DocumentTypeEnum.OTHER),
    (re.compile(r"^intake$", re.I), DocumentTypeEnum.OTHER),  # resolved by filename below
)

# File names (brd.txt, PRD-*.md, use-cases.md, ac.txt)
_FILENAME_TYPE_PATTERNS: Sequence[tuple[re.Pattern[str], DocumentTypeEnum]] = (
    (re.compile(r"^brd\b|business[-_\s]?requirements", re.I), DocumentTypeEnum.BRD),
    (re.compile(r"^prd\b|product[-_\s]?requirements", re.I), DocumentTypeEnum.PRD),
    (re.compile(r"use[-_\s]?cases?|^uc\b", re.I), DocumentTypeEnum.UC),
    (re.compile(r"acceptance|^ac\b", re.I), DocumentTypeEnum.AC),
    # Single-file client deliverable (combined BRD/PRD/UC/AC in one PDF)
    (re.compile(r"qa[-_\s]?intake|client[-_\s]?intake|intake[-_\s]?package", re.I), DocumentTypeEnum.BRD),
)

_INTAKE_BASENAME_MAP = {
    "brd": DocumentTypeEnum.BRD,
    "prd": DocumentTypeEnum.PRD,
    "uc": DocumentTypeEnum.UC,
    "ac": DocumentTypeEnum.AC,
}

_SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv"}
# Re-export for connectors that filter local walks
_SKIP_EXTENSIONS = SKIP_EXTENSIONS

# Soft intake roles (non-blocking signals)
SOFT_INTAKE_ROLES = ("PRIMARY_SPEC", "ASSET_BUNDLE", "SUPPLEMENTARY", "FIXTURE_FILE")


def infer_soft_intake_role(relative_path: str, filename: str, mime_type: str = "") -> str:
    """Non-blocking classification for content-first intake."""
    name = filename.lower()
    if "entregable" in name or "set de pruebas" in name:
        return "SUPPLEMENTARY"
    if name.endswith((".xlsx", ".xls", ".csv")):
        if any(tok in name for tok in ("credencial", "credential", "usuario", "user", "test-credentials", "login")):
            return "ASSET_BUNDLE"
        if any(tok in name for tok in ("inscripcion", "data", "datos", "transfer", "formato")):
            return "ASSET_BUNDLE"
        return "ASSET_BUNDLE"
    if name.endswith(".pdf") and ("prueba" in name or re.search(r"inc\d+", name, re.I)):
        return "PRIMARY_SPEC"
    if name.endswith((".txt", ".dat", ".csv")):
        return "FIXTURE_FILE"
    return "SUPPLEMENTARY"


class DiscoverySource(str, Enum):
    DRIVE = "drive"
    LOCAL = "local"


@dataclass
class DiscoveredFolder:
    relative_path: str
    name: str
    drive_folder_id: Optional[str] = None
    parent_relative_path: str = ""


@dataclass
class DiscoveredFile:
    file_id: str
    name: str
    relative_path: str
    mime_type: str = "application/octet-stream"
    modified_at: Optional[str] = None
    size_bytes: Optional[int] = None
    fingerprint: str = ""
    inferred_doc_type: DocumentTypeEnum = DocumentTypeEnum.OTHER
    source: DiscoverySource = DiscoverySource.DRIVE
    local_path: Optional[str] = None
    folder_segments: List[str] = field(default_factory=list)

    def to_snapshot_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "relative_path": self.relative_path,
            "file_name": self.name,
            "mime_type": self.mime_type,
            "modified_at": self.modified_at,
            "fingerprint": self.fingerprint,
            "inferred_doc_type": self.inferred_doc_type.value,
        }


@dataclass
class DiscoveryResult:
    root_name: str
    root_folder_id: str
    folders: List[DiscoveredFolder]
    files: List[DiscoveredFile]
    source: DiscoverySource

    @property
    def folder_paths(self) -> List[str]:
        return [f.relative_path for f in self.folders if f.relative_path]

    def files_by_type(self, doc_type: DocumentTypeEnum) -> List[DiscoveredFile]:
        return [f for f in self.files if f.inferred_doc_type == doc_type]

    def best_mandatory_set(self) -> List[DiscoveredFile]:
        """Pick strongest file per mandatory type (BRD, PRD, UC, AC) for readiness."""
        picked: Dict[DocumentTypeEnum, DiscoveredFile] = {}
        priority = {DocumentTypeEnum.BRD: 0, DocumentTypeEnum.PRD: 1, DocumentTypeEnum.UC: 2, DocumentTypeEnum.AC: 3}
        for f in sorted(self.files, key=lambda x: (priority.get(x.inferred_doc_type, 99), x.relative_path)):
            if f.inferred_doc_type in priority and f.inferred_doc_type not in picked:
                picked[f.inferred_doc_type] = f
        return list(picked.values())

    def has_combined_intake_file(self) -> bool:
        """True when a single deliverable likely contains all requirement sections."""
        if len(self.files) != 1:
            return False
        name = self.files[0].name.lower()
        return any(token in name for token in ("qa-intake", "client-intake", "intake-package", "intake_package"))


def classify_document(relative_path: str, filename: str, text_preview: str = "") -> DocumentTypeEnum:
    """Infer document type from folder scaffolding, file name, and optional content."""
    segments = [s for s in Path(relative_path).parts if s and s != filename]
    for segment in segments:
        for pattern, doc_type in _FOLDER_TYPE_PATTERNS:
            if pattern.search(segment):
                if doc_type != DocumentTypeEnum.OTHER:
                    return doc_type
                # intake/ folder — use basename
                stem = Path(filename).stem.lower()
                if stem in _INTAKE_BASENAME_MAP:
                    return _INTAKE_BASENAME_MAP[stem]

    for pattern, doc_type in _FILENAME_TYPE_PATTERNS:
        if pattern.search(filename):
            return doc_type

    if text_preview:
        from src.intake.parser import infer_document_type

        return infer_document_type(filename, text_preview)

    return DocumentTypeEnum.OTHER


def soft_classify_file(filename: str, text_preview: str = "", *, sheet_count: int = 0) -> str:
    """Non-blocking soft role: PRIMARY_SPEC | ASSET_BUNDLE | SUPPLEMENTARY | FIXTURE_FILE."""
    from src.intake.parser import infer_soft_role

    return infer_soft_role(filename, text_preview, sheet_count=sheet_count)


def build_fingerprint(modified_at: Optional[str], size_bytes: Optional[int], file_id: str = "") -> str:
    return f"{modified_at or ''}:{size_bytes or 0}:{file_id}"


def discover_local_tree(root: Path, root_id: str = "local-root") -> DiscoveryResult:
    """Walk a local directory tree for offline discovery."""
    root = root.resolve()
    folders: List[DiscoveredFolder] = []
    files: List[DiscoveredFile] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir != ".":
            folders.append(
                DiscoveredFolder(
                    relative_path=rel_dir,
                    name=Path(dirpath).name,
                    parent_relative_path=Path(rel_dir).parent.as_posix() if "/" in rel_dir else "",
                )
            )

        for fname in filenames:
            path = Path(dirpath) / fname
            if not is_intake_file(fname):
                continue
            rel_file = path.relative_to(root).as_posix()
            stat = path.stat()
            modified = str(int(stat.st_mtime))
            discovered = DiscoveredFile(
                file_id=f"local-{rel_file}",
                name=fname,
                relative_path=rel_file,
                mime_type=guess_mime_from_filename(fname),
                modified_at=modified,
                size_bytes=stat.st_size,
                fingerprint=build_fingerprint(modified, stat.st_size, rel_file),
                inferred_doc_type=classify_document(rel_file, fname),
                source=DiscoverySource.LOCAL,
                local_path=str(path),
                folder_segments=list(Path(rel_file).parent.parts),
            )
            files.append(discovered)

    return DiscoveryResult(
        root_name=root.name,
        root_folder_id=root_id,
        folders=folders,
        files=files,
        source=DiscoverySource.LOCAL,
    )


def discover_from_drive_entries(
    entries: List[Dict[str, Any]],
    root_folder_id: str,
    root_name: str = "",
) -> DiscoveryResult:
    """Build discovery result from recursive Drive list (each entry has relative_path)."""
    folders: List[DiscoveredFolder] = []
    files: List[DiscoveredFile] = []
    seen_folder_paths: set[str] = set()

    for entry in entries:
        rel = (entry.get("relative_path") or entry.get("name") or "").strip("/")
        if not rel:
            continue
        mime = entry.get("mimeType", "")
        if mime == "application/vnd.google-apps.folder":
            if rel not in seen_folder_paths:
                seen_folder_paths.add(rel)
                folders.append(
                    DiscoveredFolder(
                        relative_path=rel,
                        name=Path(rel).name,
                        drive_folder_id=entry.get("id"),
                        parent_relative_path=Path(rel).parent.as_posix() if "/" in rel else "",
                    )
                )
            continue

        name = entry.get("name", Path(rel).name)
        if not is_intake_file(name, mime):
            continue
        modified = entry.get("modifiedTime")
        size = entry.get("size")
        file_id = entry["id"]
        files.append(
            DiscoveredFile(
                file_id=file_id,
                name=name,
                relative_path=rel,
                mime_type=mime or guess_mime_from_filename(name),
                modified_at=modified,
                size_bytes=int(size) if size is not None else None,
                fingerprint=build_fingerprint(modified, int(size) if size else None, file_id),
                inferred_doc_type=classify_document(rel, name),
                source=DiscoverySource.DRIVE,
                folder_segments=list(Path(rel).parent.parts),
            )
        )

        # Register intermediate folder paths
        parent = Path(rel).parent
        while str(parent) != ".":
            pstr = parent.as_posix()
            if pstr not in seen_folder_paths:
                seen_folder_paths.add(pstr)
                folders.append(
                    DiscoveredFolder(
                        relative_path=pstr,
                        name=parent.name,
                        parent_relative_path=parent.parent.as_posix() if parent.parent != Path(".") else "",
                    )
                )
            parent = parent.parent

    return DiscoveryResult(
        root_name=root_name or root_folder_id,
        root_folder_id=root_folder_id,
        folders=folders,
        files=files,
        source=DiscoverySource.DRIVE,
    )


