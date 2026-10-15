"""Resolve target URL from context, metadata, and live operator hints."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.intake.metadata import extract_intake_metadata_from_text
from src.services.run_hints import get_run_hints

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

# Allowlist for demo / known target URLs. Sub-domains of allowed hosts are accepted.
_ALLOWED_DEMO_HOSTS = (
    "bank-portal.up.railway.app",
    "localhost",
    "127.0.0.1",
    "localhost.up.railway.app",
)
_ALLOWED_DEMO_HOST_SUFFIXES = (
    ".up.railway.app",
)


def is_demo_target_url_allowed(url: Optional[str]) -> bool:
    """Return True when the target URL is on the demo allowlist."""
    if not url:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _ALLOWED_DEMO_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in _ALLOWED_DEMO_HOST_SUFFIXES)


def extract_target_url_from_documents(documents: List[Dict[str, Any]]) -> Optional[str]:
    for doc in documents or []:
        extracted = extract_intake_metadata_from_text(doc.get("raw_text") or "")
        url = (extracted.get("target_url") or "").strip()
        if url:
            return url
        for match in _URL_RE.findall(doc.get("raw_text") or ""):
            if "example.com" not in match.lower():
                return match.strip()
    return None


def resolve_target_url_from_sources(
    documents: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    run_id: str = "",
) -> Optional[str]:
    """Strict resolution: metadata, operator hints, then intake document text."""
    meta = metadata or {}
    hints = get_run_hints(run_id) if run_id else {}
    for candidate in (
        meta.get("target_url"),
        hints.get("target_url"),
        extract_target_url_from_documents(documents),
    ):
        if candidate and str(candidate).strip():
            url = str(candidate).strip()
            if url.startswith("http"):
                return url
    return None


def intake_has_target_url(
    documents: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    run_id: str = "",
) -> bool:
    return resolve_target_url_from_sources(documents, metadata, run_id) is not None


def resolve_target_url(context: Dict[str, Any]) -> Optional[str]:
    run_id = context.get("run_id") or context.get("execution_id") or ""
    metadata = context.get("metadata") or {}
    documents = context.get("documents") or []
    interpreted = context.get("interpreted_intake") or {}
    env_url = (interpreted.get("environment") or {}).get("target_url")

    for candidate in (
        context.get("target_url"),
        resolve_target_url_from_sources(documents, metadata, run_id),
        env_url,
        (context.get("test_plan") or {}).get("target_url"),
    ):
        if candidate and str(candidate).strip():
            url = str(candidate).strip()
            if url.startswith("http") and "example.com" not in url.lower():
                return url
    for candidate in (
        context.get("target_url"),
        resolve_target_url_from_sources(documents, metadata, run_id),
        env_url,
    ):
        if candidate and str(candidate).strip().startswith("http"):
            return str(candidate).strip()
    return None
