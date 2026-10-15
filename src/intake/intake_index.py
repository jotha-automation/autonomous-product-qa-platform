"""Searchable intake index — full-text + sheet/cell references for MCP tools."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IndexEntry:
    file_id: str
    filename: str
    relative_path: str
    location: str
    text: str
    sheet: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "relative_path": self.relative_path,
            "location": self.location,
            "snippet": self.text[:300],
            "sheet": self.sheet,
            "row": self.row,
            "col": self.col,
        }


@dataclass
class IntakeIndex:
    project_id: str
    entries: List[IndexEntry] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)

    def search(self, query: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        if not query.strip():
            return []
        pattern = re.compile(re.escape(query), re.I)
        hits: List[Dict[str, Any]] = []
        for entry in self.entries:
            if pattern.search(entry.text) or pattern.search(entry.filename):
                hits.append(entry.to_dict())
                if len(hits) >= limit:
                    break
        return hits

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "file_count": len(self.files),
            "entry_count": len(self.entries),
            "files": self.files,
        }

    def list_files(self) -> List[Dict[str, Any]]:
        return list(self.files)

    def get_entry(self, file_id_or_path: str) -> Optional[IndexEntry]:
        key = file_id_or_path.lower()
        for entry in self.entries:
            if (
                entry.file_id.lower() == key
                or entry.filename.lower() == key
                or entry.relative_path.lower() == key
                or key in entry.relative_path.lower()
            ):
                return entry
        return None

    def find_by_hint(self, hint: str) -> List[Dict[str, Any]]:
        hint_lower = hint.lower().replace("\\", "/")
        hint_name = hint_lower.split("/")[-1]
        matches = []
        seen = set()
        for f in self.files:
            rel = (f.get("relative_path") or "").lower()
            fname = (f.get("filename") or "").lower()
            if hint_lower in rel or hint_name in fname or fname in hint_lower:
                fid = f.get("file_id")
                if fid not in seen:
                    seen.add(fid)
                    matches.append(f)
        return matches

    def search_text(self, query: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        return self.search(query, limit=limit)


def build_intake_index(project_id: str, documents: List[Dict[str, Any]]) -> IntakeIndex:
    """Build searchable index from canonical document dicts."""
    index = IntakeIndex(project_id=project_id)
    for doc in documents:
        file_id = doc.get("id") or doc.get("drive_file_id") or doc.get("filename", "")
        filename = doc.get("filename", "")
        rel = doc.get("relative_path", filename)
        extra = doc.get("extra") or {}
        intake_role = extra.get("intake_role", "SUPPLEMENTARY")
        index.files.append(
            {
                "file_id": file_id,
                "filename": filename,
                "relative_path": rel,
                "document_type": doc.get("document_type"),
                "intake_role": intake_role,
                "mime_hint": extra.get("format"),
            }
        )

        raw = doc.get("raw_text") or ""
        if raw:
            for chunk_start in range(0, len(raw), 2000):
                chunk = raw[chunk_start : chunk_start + 2000]
                index.entries.append(
                    IndexEntry(
                        file_id=file_id,
                        filename=filename,
                        relative_path=rel,
                        location=f"text:{chunk_start}",
                        text=chunk,
                    )
                )

        sheet_meta = extra.get("sheet_metadata") or {}
        tables = doc.get("tables") or extra.get("tables") or []
        sheets = extra.get("sheets") or []
        if sheets:
            for sheet in sheets:
                name = sheet.get("name", "")
                for ri, row in enumerate(sheet.get("rows") or []):
                    row_text = " | ".join(str(c) for c in row if c)
                    if not row_text.strip():
                        continue
                    index.entries.append(
                        IndexEntry(
                            file_id=file_id,
                            filename=filename,
                            relative_path=rel,
                            location=f"{name}!{ri + 1}",
                            text=row_text,
                            sheet=name,
                            row=ri + 1,
                        )
                    )
        elif tables and sheet_meta:
            for sheet_name, meta in sheet_meta.items():
                headers = meta.get("headers") or []
                if headers:
                    index.entries.append(
                        IndexEntry(
                            file_id=file_id,
                            filename=filename,
                            relative_path=rel,
                            location=f"{sheet_name}!headers",
                            text=" | ".join(str(h) for h in headers),
                            sheet=sheet_name,
                            row=1,
                        )
                    )

        pages = extra.get("pages") or []
        for page in pages:
            text = page.get("text", "")
            if text.strip():
                index.entries.append(
                    IndexEntry(
                        file_id=file_id,
                        filename=filename,
                        relative_path=rel,
                        location=f"page:{page.get('page', 0)}",
                        text=text[:4000],
                    )
                )

    return index
