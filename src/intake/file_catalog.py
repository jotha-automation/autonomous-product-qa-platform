"""Classify intake files by purpose and link supporting assets to primary specs."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.intake.qa_spec_chunker import is_qa_spec_text

PRIMARY_SPEC = "PRIMARY_SPEC"
ASSET_BUNDLE = "ASSET_BUNDLE"
FIXTURE_FILE = "FIXTURE_FILE"
SUPPLEMENTARY = "SUPPLEMENTARY"

_CREDENTIAL_NAME = re.compile(r"(credencial|credential|usuario|user|login|auth)", re.I)
_DATA_NAME = re.compile(r"(test[-_]?data|datos|data|transfer|inscripcion|formato)", re.I)
_FIXTURE_NAME = re.compile(r"(fixture|sample|mock|plantilla|template)", re.I)
_SPEC_NAME = re.compile(r"(prueba|testlink|test[-_]?case|qa[-_]?spec|inc\d+)", re.I)

_HEADER_ALIASES = {
    "cliente": ("cliente", "codigo cliente", "código cliente", "cod cliente"),
    "documento": ("numdocumento", "num documento", "documento", "número de documento", "numero de documento"),
    "tipo_id": ("tipoid", "tipo id", "tipo de identificacion", "tipo de identificación"),
    "clave": ("clave", "password", "contraseña", "contrasena"),
    "token": ("token", "otp"),
    "celular": ("celular", "mobile", "phone"),
}


def _doc_key(doc: dict) -> str:
    return str(doc.get("drive_file_id") or doc.get("id") or doc.get("filename") or "")


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _match_header(header: str) -> Optional[str]:
    norm = _normalize_header(header)
    for canonical, aliases in _HEADER_ALIASES.items():
        if norm in aliases or norm.replace(" ", "") in {a.replace(" ", "") for a in aliases}:
            return canonical
    return None


def classify_file_purpose(doc: dict) -> Dict[str, Any]:
    """Infer role, purpose, and usage hints for a single intake document."""
    filename = doc.get("filename") or ""
    name = filename.lower()
    extra = doc.get("extra") or {}
    text = (doc.get("raw_text") or "")[:12000]
    sheets = extra.get("sheets") or []
    sheet_names = [str(s.get("name", "")).lower() for s in sheets]
    fmt = extra.get("format") or (name.rsplit(".", 1)[-1] if "." in name else "")

    role = extra.get("intake_role") or extra.get("soft_intake_role") or SUPPLEMENTARY
    purpose = "Supporting document"
    usage_when: List[str] = []
    asset_types: List[str] = []

    if fmt == "pdf" or name.endswith(".pdf"):
        if is_qa_spec_text(text, filename) or _SPEC_NAME.search(name):
            role = PRIMARY_SPEC
            purpose = "QA test specification (TestLink / casos de prueba)"
            usage_when = [
                "obligation_extraction",
                "test_design",
                "playwright_scenario_planning",
            ]
        elif len(text) > 15000:
            role = PRIMARY_SPEC
            purpose = "Primary requirements / combined specification PDF"
            usage_when = ["requirements_analysis", "test_design"]

    elif fmt in {"xlsx", "xls"} or name.endswith((".xlsx", ".xls")):
        role = ASSET_BUNDLE
        has_users = any("usuario" in sn or sn in {"users", "credenciales"} for sn in sheet_names)
        has_sensitive = any(
            _match_header(h)
            for s in sheets
            for h in (s.get("headers") or [])
        )
        if _CREDENTIAL_NAME.search(name) or has_users or has_sensitive:
            asset_types.append("credential")
            usage_when.append("playwright_login")
        if _DATA_NAME.search(name) or any(sn in {"data", "datos"} for sn in sheet_names):
            asset_types.append("transfer_test_data")
            usage_when.append("inscription_scenarios")
        if any("destino" in sn for sn in sheet_names):
            asset_types.append("destination_accounts")
            usage_when.append("transfer_destinations")
        if not asset_types:
            asset_types.append("tabular_data")
            usage_when.append("test_data_lookup")
        purpose = "Test asset bundle (credentials and/or execution data)"
        if _CREDENTIAL_NAME.search(name):
            purpose = "Credentials and test data for automated execution"

    elif fmt in {"csv", "txt", "dat"} or _FIXTURE_NAME.search(name):
        role = FIXTURE_FILE
        purpose = "Fixture file (plain data / sample upload)"
        usage_when = ["file_upload_tests", "data_driven_steps"]
        asset_types.append("fixture_file")

    elif role == SUPPLEMENTARY and extra.get("soft_intake_role"):
        role = extra["soft_intake_role"]

    return {
        "intake_role": role,
        "purpose": purpose,
        "usage_when": usage_when,
        "asset_types": asset_types,
        "supports_modules": _infer_modules(text, sheets),
    }


def _infer_modules(text: str, sheets: List[dict]) -> List[str]:
    blob = text.lower()
    for s in sheets:
        blob += " " + str(s.get("name", "")).lower()
        for row in (s.get("rows") or [])[:20]:
            blob += " " + " ".join(str(c) for c in row if c).lower()
    modules = []
    for token, label in (
        ("inscripcion", "inscripciones"),
        ("transfer", "transferencias"),
        ("pago", "pagos"),
        ("consulta", "consultas"),
        ("login", "login"),
    ):
        if token in blob:
            modules.append(label)
    return sorted(set(modules))


def build_intake_catalog(documents: List[dict]) -> Dict[str, Any]:
    """Build folder-level catalog linking specs to supporting files."""
    entries: Dict[str, Dict[str, Any]] = {}
    primaries: List[dict] = []

    for doc in documents:
        key = _doc_key(doc)
        meta = classify_file_purpose(doc)
        entry = {
            "file_id": key,
            "filename": doc.get("filename"),
            "relative_path": doc.get("relative_path", ""),
            "document_type": doc.get("document_type"),
            **meta,
            "linked_primary_id": None,
        }
        entries[key] = entry
        if meta["intake_role"] == PRIMARY_SPEC:
            primaries.append(doc)

    primary = _pick_primary_spec(primaries, documents)
    primary_id = _doc_key(primary) if primary else None

    for key, entry in entries.items():
        if entry["intake_role"] in {ASSET_BUNDLE, FIXTURE_FILE} and primary_id:
            entry["linked_primary_id"] = primary_id
            entry["usage_when"].append("linked_to_primary_spec")
        if entry["intake_role"] == PRIMARY_SPEC:
            entry["linked_primary_id"] = key

    support_files = [
        {"file_id": k, "filename": v["filename"], "purpose": v["purpose"], "asset_types": v["asset_types"]}
        for k, v in entries.items()
        if v["intake_role"] in {ASSET_BUNDLE, FIXTURE_FILE}
    ]

    return {
        "primary_spec_id": primary_id,
        "primary_spec_filename": primary.get("filename") if primary else None,
        "files": entries,
        "support_files": support_files,
        "file_count": len(entries),
        "summary": _catalog_summary(entries, primary),
    }


def _pick_primary_spec(primaries: List[dict], all_docs: List[dict]) -> Optional[dict]:
    if len(primaries) == 1:
        return primaries[0]
    if primaries:
        return max(primaries, key=lambda d: len(d.get("raw_text") or ""))
    for doc in all_docs:
        if is_qa_spec_text(doc.get("raw_text") or "", doc.get("filename", "")):
            return doc
    return all_docs[0] if len(all_docs) == 1 else None


def _catalog_summary(entries: Dict[str, Dict], primary: Optional[dict]) -> str:
    if not entries:
        return "No intake files"
    parts = [f"{len(entries)} file(s)"]
    if primary:
        parts.append(f"spec: {primary.get('filename')}")
    bundles = [e["filename"] for e in entries.values() if e["intake_role"] == ASSET_BUNDLE]
    if bundles:
        parts.append(f"assets: {', '.join(bundles)}")
    return "; ".join(parts)


def enrich_documents_with_catalog(documents: List[dict]) -> Tuple[List[dict], Dict[str, Any]]:
    """Apply catalog roles and linkage metadata to parsed documents."""
    catalog = build_intake_catalog(documents)
    enriched: List[dict] = []

    for doc in documents:
        doc = dict(doc)
        key = _doc_key(doc)
        entry = catalog["files"].get(key, {})
        extra = dict(doc.get("extra") or {})
        if entry:
            extra["intake_role"] = entry.get("intake_role", extra.get("intake_role", SUPPLEMENTARY))
            extra["file_purpose"] = entry.get("purpose")
            extra["usage_when"] = entry.get("usage_when", [])
            extra["asset_types"] = entry.get("asset_types", [])
            extra["supports_modules"] = entry.get("supports_modules", [])
            if entry.get("linked_primary_id"):
                extra["linked_primary_id"] = entry["linked_primary_id"]
        doc["extra"] = extra
        doc["intake_role"] = extra.get("intake_role", SUPPLEMENTARY)
        enriched.append(doc)

    return enriched, catalog


def support_files_for_ingest(discovery_files: List[Any]) -> set[str]:
    """Paths that must always be ingested alongside mandatory portfolio files."""
    from src.intake.discovery import infer_soft_intake_role

    paths: set[str] = set()
    for item in discovery_files:
        role = infer_soft_intake_role(item.relative_path, item.name, item.mime_type)
        if role in {ASSET_BUNDLE, FIXTURE_FILE, PRIMARY_SPEC}:
            paths.add(item.relative_path)
        name = item.name.lower()
        if name.endswith((".xlsx", ".xls", ".csv")) or _CREDENTIAL_NAME.search(name):
            paths.add(item.relative_path)
        if _SPEC_NAME.search(name) and name.endswith(".pdf"):
            paths.add(item.relative_path)
    return paths
