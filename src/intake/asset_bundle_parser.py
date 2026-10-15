"""Parse ASSET_BUNDLE spreadsheets into typed test assets and runtime secrets."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from src.intake.file_catalog import _match_header, _normalize_header
from src.intake.schemas.interpreted import SourceRef, TestAsset

SENSITIVE_HEADERS = re.compile(r"(password|passwd|clave|token|secret|otp|pin|credential)", re.I)

SHEET_CREDENTIAL = re.compile(r"usuario|user|credencial|credential|login", re.I)
SHEET_DATA = re.compile(r"^data$|^datos$|escenario|transaccion", re.I)
SHEET_DESTINATIONS = re.compile(r"destino", re.I)


def _normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _row_matches_headers(headers: List[str], row: List[Any]) -> bool:
    if not headers or not row:
        return False
    for i, header in enumerate(headers):
        if not header:
            continue
        if i >= len(row):
            return False
        if _normalize_header(str(header)) != _normalize_header(_normalize_cell(row[i])):
            return False
    return True


def _row_to_fields(headers: List[str], row: List[Any]) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for i, header in enumerate(headers):
        if not header or i >= len(row):
            continue
        canonical = _match_header(str(header))
        key = canonical or _normalize_header(str(header)).replace(" ", "_")
        val = _normalize_cell(row[i])
        if val:
            fields[key] = val
    return fields


def _tipo_id_short(value: str) -> str:
    low = value.lower()
    if "ciudadan" in low or low == "cc":
        return "CC"
    if "extranjer" in low or low == "ce":
        return "CE"
    if low == "nit":
        return "NIT"
    return value[:20]


def parse_asset_bundle_document(doc: dict) -> List[Tuple[TestAsset, Dict[str, str]]]:
    """Extract typed assets from an Excel/CSV asset bundle document."""
    extra = doc.get("extra") or {}
    role = extra.get("intake_role") or doc.get("intake_role") or ""
    if role != "ASSET_BUNDLE":
        return []

    sheets = extra.get("sheets") or []
    if not sheets:
        return []

    filename = doc.get("filename") or "asset_bundle"
    results: List[Tuple[TestAsset, Dict[str, str]]] = []

    for sheet in sheets:
        sheet_name = str(sheet.get("name") or "")
        headers = [str(h) if h is not None else "" for h in (sheet.get("headers") or [])]
        rows = sheet.get("rows") or []
        if not headers and rows:
            headers = [str(h) if h is not None else "" for h in rows[0]]
            rows = rows[1:]
        elif headers and rows and _row_matches_headers(headers, rows[0]):
            rows = rows[1:]

        if SHEET_CREDENTIAL.search(sheet_name) or _headers_look_like_credentials(headers):
            results.extend(_parse_credential_sheet(filename, sheet_name, headers, rows))

        elif SHEET_DESTINATIONS.search(sheet_name):
            results.extend(_parse_tabular_sheet(filename, sheet_name, headers, rows, asset_type="tabular_row", usage="destination_accounts"))

        elif SHEET_DATA.search(sheet_name) or sheet_name.lower() in {"data", "datos"}:
            results.extend(_parse_tabular_sheet(filename, sheet_name, headers, rows, asset_type="tabular_row", usage="transfer_scenario"))

        elif rows:
            results.extend(_parse_tabular_sheet(filename, sheet_name, headers, rows, asset_type="tabular_row", usage="tabular_data"))

    return results


def _headers_look_like_credentials(headers: List[str]) -> bool:
    canonical = {_match_header(h) for h in headers if h}
    return "cliente" in canonical or ("clave" in canonical and "token" in canonical)


def _parse_credential_sheet(
    filename: str, sheet_name: str, headers: List[str], rows: List[List[Any]]
) -> List[Tuple[TestAsset, Dict[str, str]]]:
    out: List[Tuple[TestAsset, Dict[str, str]]] = []
    cred_index = 0
    for ri, row in enumerate(rows, start=2):
        raw = _row_to_fields(headers, row)
        if not raw.get("cliente") and not raw.get("clave"):
            continue
        if raw.get("tipo_id"):
            raw["tipo_id"] = _tipo_id_short(raw["tipo_id"])
        if "numdocumento" in raw and "documento" not in raw:
            raw["documento"] = raw.pop("numdocumento")

        cred_index += 1
        key = "login_user" if cred_index == 1 else f"login_user_{cred_index}"
        sensitive = [k for k in raw if k in {"clave", "token", "password", "otp", "pin"} or SENSITIVE_HEADERS.search(k)]
        display = {k: ("[REDACTED]" if k in sensitive else v) for k, v in raw.items()}
        out.append(
            (
                TestAsset(
                    key=key,
                    asset_type="credential",
                    source_ref=SourceRef(filename=filename, location=f"{sheet_name}!{ri}"),
                    fields=display,
                    usage_hint="bank portal login and authenticated flows",
                    sensitive_field_keys=sensitive,
                ),
                raw,
            )
        )
    return out


def _parse_tabular_sheet(
    filename: str,
    sheet_name: str,
    headers: List[str],
    rows: List[List[Any]],
    *,
    asset_type: str,
    usage: str = "tabular_data",
) -> List[Tuple[TestAsset, Dict[str, str]]]:
    out: List[Tuple[TestAsset, Dict[str, str]]] = []
    for ri, row in enumerate(rows[:100], start=2):
        raw = _row_to_fields(headers, row)
        if not any(raw.values()):
            continue
        sensitive = [k for k, v in raw.items() if SENSITIVE_HEADERS.search(k)]
        key = f"{usage}_{sheet_name.lower().replace(' ', '_')}_row_{ri}"
        display = {k: ("[REDACTED]" if k in sensitive else v) for k, v in raw.items()}
        out.append(
            (
                TestAsset(
                    key=key,
                    asset_type=asset_type,
                    source_ref=SourceRef(filename=filename, location=f"{sheet_name}!{ri}"),
                    fields=display,
                    usage_hint=f"{usage} from {sheet_name}",
                    sensitive_field_keys=sensitive,
                ),
                raw,
            )
        )
        if len(out) >= 50:
            break
    return out

