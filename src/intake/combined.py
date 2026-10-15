"""Split single-file client deliverables into logical BRD/PRD/UC/AC documents."""
from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from src.intake.parser import extract_sections
from src.intake.schemas.canonical import DocumentTypeEnum

_SECTION_SPECS: Sequence[Tuple[re.Pattern[str], DocumentTypeEnum, str]] = (
    (re.compile(r"BUSINESS REQUIREMENTS DOCUMENT", re.I), DocumentTypeEnum.BRD, "BRD"),
    (re.compile(r"PRODUCT REQUIREMENTS DOCUMENT", re.I), DocumentTypeEnum.PRD, "PRD"),
    (re.compile(r"USE CASE DOCUMENT", re.I), DocumentTypeEnum.UC, "UC"),
    (re.compile(r"ACCEPTANCE CRITERIA", re.I), DocumentTypeEnum.AC, "AC"),
)

_COMBINED_FILENAME_HINT = re.compile(r"qa[-_\s]?intake|client[-_\s]?intake|intake[-_\s]?package", re.I)


def _section_hits(text: str) -> List[Tuple[int, DocumentTypeEnum, str]]:
    hits: List[Tuple[int, DocumentTypeEnum, str]] = []
    for pattern, doc_type, _label in _SECTION_SPECS:
        match = pattern.search(text)
        if match:
            hits.append((match.start(), doc_type, match.group(0)))
    hits.sort(key=lambda item: item[0])
    return hits


def is_combined_intake_document(doc: Dict) -> bool:
    raw = (doc.get("raw_text") or "").strip()
    if not raw:
        return False
    hits = _section_hits(raw)
    if len(hits) >= 2:
        return True
    filename = doc.get("filename") or doc.get("name") or ""
    return bool(_COMBINED_FILENAME_HINT.search(filename)) and len(hits) >= 1


def split_combined_intake_document(doc: Dict) -> List[Dict]:
    """Expand one combined PDF/text deliverable into typed virtual documents."""
    raw = doc.get("raw_text") or ""
    hits = _section_hits(raw)
    if not hits:
        return [doc]

    base_id = doc.get("id") or doc.get("drive_file_id") or doc.get("filename") or "intake"
    base_name = doc.get("filename") or "combined-intake"
    stem = base_name.rsplit(".", 1)[0] if "." in base_name else base_name
    shared = {
        k: v
        for k, v in doc.items()
        if k not in {"id", "filename", "document_type", "raw_text", "sections", "metadata"}
    }

    virtual_docs: List[Dict] = []
    for index, (start, doc_type, header) in enumerate(hits):
        end = hits[index + 1][0] if index + 1 < len(hits) else len(raw)
        chunk = raw[start:end].strip()
        if not chunk:
            continue
        suffix = doc_type.value.lower()
        virtual = {
            **shared,
            "id": f"{base_id}-{suffix}",
            "filename": f"{stem}-{suffix}.txt",
            "document_type": doc_type.value,
            "raw_text": chunk,
            "sections": [s.model_dump() if hasattr(s, "model_dump") else s for s in extract_sections(chunk)],
            "source_combined_document": base_name,
            "combined_section_header": header,
        }
        metadata = dict(doc.get("metadata") or {})
        metadata["document_type"] = doc_type.value
        metadata["source_combined_document"] = base_name
        virtual["metadata"] = metadata
        virtual_docs.append(virtual)

    return virtual_docs or [doc]


def expand_combined_intake_documents(documents: List[Dict]) -> List[Dict]:
    """Return documents list with combined client deliverables split by section."""
    expanded: List[Dict] = []
    for doc in documents:
        if is_combined_intake_document(doc):
            expanded.extend(split_combined_intake_document(doc))
        else:
            expanded.append(doc)
    return expanded
