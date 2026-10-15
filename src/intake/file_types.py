"""Supported intake file types for local discovery and Google Drive sync."""
from pathlib import Path
from typing import Dict, FrozenSet, Optional

# User-facing intake formats (local + Drive binary download)
SUPPORTED_EXTENSIONS: FrozenSet[str] = frozenset(
    {
        ".xlsx",
        ".xls",
        ".docx",
        ".doc",
        ".pdf",
        ".md",
        ".txt",
        ".jpg",
        ".jpeg",
        ".png",
        ".svg",
        ".csv",  # Google Sheets export
        ".json",  # manifest.json
    }
)

# Archives only — not parsed as requirements
SKIP_EXTENSIONS: FrozenSet[str] = frozenset({".zip"})

IMAGE_EXTENSIONS: FrozenSet[str] = frozenset({".jpg", ".jpeg", ".png", ".svg"})

# Google native → export MIME for download / parsing
GOOGLE_EXPORT_MIME: Dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "image/svg+xml",
}

SUPPORTED_BINARY_MIMES: FrozenSet[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/msword",
        "application/vnd.ms-excel",
        "application/octet-stream",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
    }
)

_EXT_TO_MIME: Dict[str, str] = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def guess_mime_from_filename(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return _EXT_TO_MIME.get(ext, "application/octet-stream")


def is_exportable_google_app(mime: str) -> bool:
    return mime in GOOGLE_EXPORT_MIME


def is_intake_file(filename: str, mime: Optional[str] = None) -> bool:
    """Whether a file should be discovered, downloaded, and parsed."""
    ext = Path(filename).suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return False
    if ext in SUPPORTED_EXTENSIONS:
        return True
    mime = (mime or "").strip()
    if mime == "application/vnd.google-apps.folder":
        return False
    if is_exportable_google_app(mime):
        return True
    if mime.startswith("image/"):
        return True
    if mime in SUPPORTED_BINARY_MIMES or mime.startswith("text/"):
        return ext in SUPPORTED_EXTENSIONS or not ext
    if mime.startswith("application/vnd.google-apps."):
        return False
    return ext in SUPPORTED_EXTENSIONS
