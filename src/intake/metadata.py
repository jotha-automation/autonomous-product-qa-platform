"""Metadata extraction from canonical documents."""
import re
from typing import Optional

from src.intake.schemas.canonical import CanonicalDocument, DocumentMetadata, DocumentTypeEnum


class MetadataExtractor:
    VERSION_RE = re.compile(r"(?:version|v)\s*[:\.]?\s*(\d+(?:\.\d+)*)", re.I)
    RELEASE_RE = re.compile(r"release\s*[:\.]?\s*([\w\.\-]+)", re.I)
    AUTHOR_RE = re.compile(r"(?:author|prepared by|owner)\s*[:\.]?\s*(.+)", re.I)
    TARGET_URL_RE = re.compile(
        r"(?:target\s*url|application\s*url|url\s*under\s*test)\s*[:\.]?\s*(https?://[^\s,)]+)",
        re.I,
    )
    PROJECT_ID_RE = re.compile(r"project\s*id\s*[:\.]?\s*([A-Za-z0-9\-]+)", re.I)

    def extract(self, doc: CanonicalDocument) -> CanonicalDocument:
        text = doc.raw_text[:5000]
        meta = doc.metadata.model_copy()
        if not meta.version:
            m = self.VERSION_RE.search(text)
            meta.version = m.group(1) if m else None
        if not meta.release:
            m = self.RELEASE_RE.search(text)
            meta.release = m.group(1) if m else None
        if not meta.author:
            m = self.AUTHOR_RE.search(text)
            meta.author = m.group(1).strip() if m else None
        if meta.document_type == DocumentTypeEnum.OTHER:
            meta.document_type = doc.document_type
        doc.metadata = meta
        return doc


def extract_intake_metadata_from_text(text: str) -> dict[str, str]:
    """Pull project metadata embedded in client PDF/text deliverables."""
    head = text[:8000]
    out: dict[str, str] = {}
    url_match = MetadataExtractor.TARGET_URL_RE.search(head)
    if url_match:
        out["target_url"] = url_match.group(1).rstrip(".")
    project_match = MetadataExtractor.PROJECT_ID_RE.search(head)
    if project_match:
        out["project_id"] = project_match.group(1).strip()
    release_match = MetadataExtractor.RELEASE_RE.search(head)
    if release_match:
        out["release"] = release_match.group(1).strip()
    return out


def enrich_metadata_from_documents(
    metadata: dict,
    documents: list[dict],
) -> dict:
    """Fill missing run metadata (e.g. target_url) from parsed intake documents."""
    merged = dict(metadata or {})
    if merged.get("target_url"):
        return merged
    for doc in documents:
        extracted = extract_intake_metadata_from_text(doc.get("raw_text") or "")
        for key, value in extracted.items():
            merged.setdefault(key, value)
        if merged.get("target_url"):
            break
    return merged
