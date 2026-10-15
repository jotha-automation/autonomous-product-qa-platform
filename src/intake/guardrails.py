"""Intake guardrails — file type, content relevance, and upload validation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.intake.file_types import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS

# Allowlist for intake uploads
ALLOWLIST_EXTENSIONS = frozenset(
    SUPPORTED_EXTENSIONS
    | {".json", ".gif", ".webp", ".dat"}  # manifest + wireframes + plain fixtures
)

BLOCKED_EXTENSIONS = frozenset(
    {
        ".exe",
        ".sh",
        ".bat",
        ".dll",
        ".cmd",
        ".ps1",
        ".msi",
        ".app",
        ".dmg",
        ".so",
        ".dylib",
        ".bin",
        ".com",
    }
)

BLOCKED_SCRIPT_EXTENSIONS = frozenset({".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".php", ".pl", ".go", ".rs"})

# Per-type size limits (bytes)
MAX_FILE_SIZE_BY_EXT: Dict[str, int] = {
    ".pdf": 50 * 1024 * 1024,
    ".xlsx": 25 * 1024 * 1024,
    ".xls": 25 * 1024 * 1024,
    ".docx": 25 * 1024 * 1024,
    ".doc": 25 * 1024 * 1024,
    ".csv": 10 * 1024 * 1024,
    ".json": 2 * 1024 * 1024,
    ".md": 5 * 1024 * 1024,
    ".txt": 5 * 1024 * 1024,
    ".dat": 2 * 1024 * 1024,
}
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024
IMAGE_MAX_FILE_SIZE = 15 * 1024 * 1024

QA_KEYWORDS = re.compile(
    r"\b(test|qa|quality|requirement|acceptance|use\s+case|scenario|paso|prueba|validaci|"
    r"oblig|caso\s+de\s+prueba|precondicion|expected|wireframe|staging|portal|login|http|brd|prd|uc-|ac-)\b",
    re.I,
)

OUT_OF_SCOPE_ONLY = re.compile(
    r"^\s*(invoice|factura|recibo|payroll|n[oó]mina|contrato\s+legal|nda\s+only)\b",
    re.I | re.M,
)

CREDENTIAL_HINTS = re.compile(
    r"\b(password|contrase[nñ]a|api[_\s-]?key|secret|token|credential|usuario|clave)\b",
    re.I,
)

EXECUTABLE_MAGICS = (
    b"MZ",  # PE
    b"\x7fELF",
    b"#!",
)


@dataclass
class GuardrailViolation:
    code: str
    message: str
    path: str = ""
    severity: str = "error"  # error | warn


@dataclass
class GuardrailResult:
    accepted: bool
    violations: List[GuardrailViolation] = field(default_factory=list)
    warnings: List[GuardrailViolation] = field(default_factory=list)
    pii_flagged_paths: List[str] = field(default_factory=list)
    qa_relevant_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "violations": [v.__dict__ for v in self.violations],
            "warnings": [w.__dict__ for w in self.warnings],
            "pii_flagged_paths": self.pii_flagged_paths,
            "qa_relevant_count": self.qa_relevant_count,
        }


class IntakeGuardrailError(RuntimeError):
    """Raised when intake fails guardrail validation."""

    def __init__(self, message: str, result: Optional[GuardrailResult] = None):
        super().__init__(message)
        self.result = result


def _ext(path: str) -> str:
    return Path(path).suffix.lower()


def is_blocked_extension(path: str) -> bool:
    ext = _ext(path)
    if ext in BLOCKED_EXTENSIONS or ext in BLOCKED_SCRIPT_EXTENSIONS:
        return True
    name = Path(path).name.lower()
    if ext == ".zip":
        return not _is_fixture_zip_path(path)
    if ext == ".json" and name != "manifest.json":
        return True
    return False


def _is_fixture_zip_path(path: str) -> bool:
    lower = path.lower().replace("\\", "/")
    return any(tok in lower for tok in ("fixtures", "test-data", "test_data", "fixture"))


def max_size_for_path(path: str) -> int:
    ext = _ext(path)
    if ext in IMAGE_EXTENSIONS or ext in {".gif", ".webp"}:
        return IMAGE_MAX_FILE_SIZE
    return MAX_FILE_SIZE_BY_EXT.get(ext, DEFAULT_MAX_FILE_SIZE)


def is_likely_encrypted_pdf(content: bytes) -> bool:
    if not content.startswith(b"%PDF"):
        return False
    head = content[:8192]
    if b"/Encrypt" in head:
        return True
    return False


def has_executable_payload(content: bytes) -> bool:
    if len(content) < 4:
        return False
    for magic in EXECUTABLE_MAGICS:
        if content.startswith(magic):
            return True
    return False


def validate_upload_file(
    relative_path: str,
    content: bytes,
    *,
    mime: str = "",
) -> List[GuardrailViolation]:
    """Validate a single file before upload/ingest."""
    violations: List[GuardrailViolation] = []
    path = relative_path.replace("\\", "/")
    name = Path(path).name
    ext = _ext(name)

    if is_blocked_extension(path):
        violations.append(
            GuardrailViolation(
                code="blocked_file_type",
                message=f"File type not allowed for QA intake: {ext or name}",
                path=path,
            )
        )
        return violations

    if ext and ext not in ALLOWLIST_EXTENSIONS and not mime.startswith("image/"):
        if not (ext == ".json" and name == "manifest.json"):
            violations.append(
                GuardrailViolation(
                    code="unsupported_extension",
                    message=f"Extension {ext} is not in the intake allowlist",
                    path=path,
                )
            )

    if not content:
        violations.append(
            GuardrailViolation(
                code="empty_file",
                message="File is empty",
                path=path,
            )
        )
        return violations

    limit = max_size_for_path(path)
    if len(content) > limit:
        violations.append(
            GuardrailViolation(
                code="file_too_large",
                message=f"File exceeds {limit // (1024 * 1024)}MB limit for {ext or 'this type'}",
                path=path,
            )
        )

    if ext == ".pdf" and is_likely_encrypted_pdf(content):
        violations.append(
            GuardrailViolation(
                code="encrypted_pdf",
                message="Password-protected or encrypted PDFs cannot be processed",
                path=path,
            )
        )

    if has_executable_payload(content) and ext not in IMAGE_EXTENSIONS:
        violations.append(
            GuardrailViolation(
                code="executable_payload",
                message="File contains executable content not suitable for QA intake",
                path=path,
            )
        )

    return violations


def validate_upload_batch(
    files: Sequence[Tuple[str, bytes, str]],
) -> GuardrailResult:
    """Validate all files in an upload batch."""
    violations: List[GuardrailViolation] = []
    warnings: List[GuardrailViolation] = []
    pii_paths: List[str] = []

    for rel, content, mime in files:
        path = rel.replace("\\", "/")
        violations.extend(validate_upload_file(path, content, mime=mime))
        text_sample = content[:4096].decode("utf-8", errors="ignore")
        if CREDENTIAL_HINTS.search(text_sample) or _ext(path) in {".xlsx", ".xls", ".csv"}:
            if CREDENTIAL_HINTS.search(text_sample) or any(
                tok in path.lower() for tok in ("credencial", "usuario", "password", "login", "user")
            ):
                pii_paths.append(path)
                warnings.append(
                    GuardrailViolation(
                        code="credential_asset",
                        message="Credential or sensitive data detected — will route to vault, not reports",
                        path=path,
                        severity="warn",
                    )
                )

    if not files:
        violations.append(
            GuardrailViolation(
                code="no_files",
                message="No files provided for intake",
            )
        )

    return GuardrailResult(
        accepted=not violations,
        violations=violations,
        warnings=warnings,
        pii_flagged_paths=pii_paths,
        qa_relevant_count=len(files),
    )


def _document_is_qa_relevant(doc: dict) -> bool:
    text = (doc.get("raw_text") or "")[:12000]
    name = (doc.get("filename") or doc.get("relative_path") or "").lower()
    dtype = str(doc.get("document_type", "")).upper()
    if dtype in {"BRD", "PRD", "UC", "AC"}:
        return True
    if QA_KEYWORDS.search(text) or QA_KEYWORDS.search(name):
        return True
    ext = _ext(name)
    if ext in IMAGE_EXTENSIONS | {".gif", ".webp"}:
        return True
    if (doc.get("extra") or {}).get("soft_intake_role") in {"PRIMARY_SPEC", "ASSET_BUNDLE", "FIXTURE_FILE"}:
        return True
    if ext in {".xlsx", ".xls", ".csv"} and any(tok in name for tok in ("data", "usuario", "credencial", "test")):
        return True
    return False


def _content_out_of_scope(doc: dict) -> bool:
    text = (doc.get("raw_text") or "")[:3000].strip()
    if not text:
        return False
    if OUT_OF_SCOPE_ONLY.search(text) and not QA_KEYWORDS.search(text):
        return True
    return False


def validate_documents_for_pipeline(documents: List[dict]) -> GuardrailResult:
    """Post-parse validation before interpretation stage."""
    violations: List[GuardrailViolation] = []
    warnings: List[GuardrailViolation] = []
    pii_paths: List[str] = []
    qa_count = 0

    if not documents:
        violations.append(
            GuardrailViolation(
                code="no_documents",
                message="No documents available after intake",
            )
        )
        return GuardrailResult(accepted=False, violations=violations)

    for doc in documents:
        path = doc.get("relative_path") or doc.get("filename") or doc.get("id") or "unknown"
        text = (doc.get("raw_text") or "").strip()
        name = str(path).lower()
        ext = _ext(name)

        if ext in BLOCKED_EXTENSIONS or ext in BLOCKED_SCRIPT_EXTENSIONS:
            violations.append(
                GuardrailViolation(
                    code="blocked_file_type",
                    message=f"Blocked file type in project: {ext}",
                    path=str(path),
                )
            )
            continue

        if not text and ext not in IMAGE_EXTENSIONS | {".gif", ".webp", ".png", ".jpg", ".jpeg", ".svg"}:
            if doc.get("parse_error"):
                violations.append(
                    GuardrailViolation(
                        code="corrupted_parse",
                        message=f"Could not extract content from {path}",
                        path=str(path),
                    )
                )
            elif ext == ".pdf":
                violations.append(
                    GuardrailViolation(
                        code="encrypted_pdf",
                        message=f"PDF appears empty or encrypted: {path}",
                        path=str(path),
                    )
                )
            else:
                warnings.append(
                    GuardrailViolation(
                        code="empty_content",
                        message=f"Minimal extractable content in {path}",
                        path=str(path),
                        severity="warn",
                    )
                )

        if _content_out_of_scope(doc):
            warnings.append(
                GuardrailViolation(
                    code="out_of_scope_content",
                    message=f"Content may be out of QA scope: {path}",
                    path=str(path),
                    severity="warn",
                )
            )

        if CREDENTIAL_HINTS.search(text[:2000]) or any(
            tok in name for tok in ("credencial", "usuario", "password", "secret")
        ):
            pii_paths.append(str(path))

        if _document_is_qa_relevant(doc):
            qa_count += 1

    if qa_count == 0 and not violations:
        violations.append(
            GuardrailViolation(
                code="no_qa_relevant_documents",
                message="Project folder has no QA-relevant documents after scan",
            )
        )

    return GuardrailResult(
        accepted=not violations,
        violations=violations,
        warnings=warnings,
        pii_flagged_paths=pii_paths,
        qa_relevant_count=qa_count,
    )


def format_violation_messages(result: GuardrailResult) -> str:
    parts = [v.message for v in result.violations]
    if not parts and result.warnings:
        parts = [w.message for w in result.warnings[:3]]
    return "; ".join(parts) if parts else "Intake guardrail validation failed"
