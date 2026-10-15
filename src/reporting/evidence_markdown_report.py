"""Markdown evidence report — one section per executed test with key screenshot."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HIGH_VALUE_LABELS = (
    "motivo",
    "rechazo",
    "detalle",
    "validar",
    "actualizar",
    "consulta",
    "resultado",
    "final",
    "evidence",
    "assert",
    "trigger",
)


def _coerce_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _slug(value: str) -> str:
    return re.sub(r"[^\w\-]+", "-", (value or "").lower()).strip("-")


def _pick_best_evidence_asset(
    test: Dict[str, Any],
    manifest_entries: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    refs = _coerce_list(test.get("evidence_refs"))
    candidates: List[Dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("media_type", "screenshot") in {"screenshot", "image"}:
            candidates.append(ref)

    test_id = str(test.get("id") or "")
    test_title = str(test.get("title") or "")
    evidence_key = str(test.get("evidence_key") or _slug(test_title))
    blob = f"{test_id} {test_title} {evidence_key}".casefold()

    for entry in manifest_entries:
        if entry.get("media_type") not in {"screenshot", "image"}:
            continue
        tid = str(entry.get("test_id") or "")
        ttitle = str(entry.get("test_title") or "")
        if (
            tid in blob
            or evidence_key in tid
            or test_title.casefold() in ttitle.casefold()
            or tid == test_id
        ):
            candidates.append(entry)

    if not candidates:
        return None

    def _score(asset: Dict[str, Any]) -> Tuple[int, int]:
        label = str(asset.get("label") or asset.get("step_id") or "").casefold()
        keyword_hits = sum(1 for kw in _HIGH_VALUE_LABELS if kw in label)
        is_failure = 1 if "fail" in str(asset.get("path") or "").casefold() else 0
        return (keyword_hits, is_failure)

    return max(candidates, key=_score)


def _relative_markdown_image(path: str, *, artifact_dir: Path) -> str:
    rel = (path or "").lstrip("/")
    if not rel:
        return ""
    if rel.startswith("evidence/"):
        return rel
    return f"evidence/{rel}" if not rel.startswith("screenshots/") else f"evidence/{rel}"


def build_evidence_markdown_report(
    dest: Path,
    *,
    run_id: str,
    project_id: str = "",
    execution_results: Optional[Dict[str, Any]] = None,
    manifest: Optional[Dict[str, Any]] = None,
    obligations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Write evidence-report.md with executed tests and best screenshot per case."""
    execution_results = execution_results or {}
    manifest = manifest or {}
    entries = _coerce_list(manifest.get("entries"))
    executed = _coerce_list(execution_results.get("executed_tests"))
    obligations = obligations or []

    ob_by_id = {str(o.get("id")): o for o in obligations if isinstance(o, dict) and o.get("id")}

    lines: List[str] = [
        f"# Informe de evidencias — {run_id}",
        "",
        f"- **Proyecto:** {project_id or '—'}",
        f"- **Estado ejecución:** {execution_results.get('status', 'unknown')}",
        f"- **Generado:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- **Tests ejecutados:** {len(executed)}",
        "",
    ]

    sections = 0
    for test in executed:
        if not isinstance(test, dict):
            continue
        title = str(test.get("title") or test.get("id") or "Test")
        status = str(test.get("status") or "unknown")
        obl_id = str(test.get("obligation_id") or test.get("id") or "")
        obl = ob_by_id.get(obl_id) or {}
        tier = obl.get("automation_tier", "")
        lines.extend(
            [
                f"## {title}",
                "",
                f"- **ID:** `{test.get('id', '')}`",
                f"- **Estado:** {status}",
                f"- **Duración:** {test.get('duration_ms', '—')} ms",
            ]
        )
        if tier:
            lines.append(f"- **Automatización:** {tier}")
        if test.get("error"):
            lines.append(f"- **Error:** {test.get('error')}")
        lines.append("")

        asset = _pick_best_evidence_asset(test, entries)
        if asset:
            rel = asset.get("screenshot_path") or asset.get("path") or asset.get("relative_path") or ""
            md_path = _relative_markdown_image(str(rel), artifact_dir=dest.parent)
            label = asset.get("label") or asset.get("step_id") or "Evidencia"
            if md_path:
                lines.append(f"### Evidencia de referencia — {label}")
                lines.append("")
                lines.append(f"![{label}]({md_path})")
                lines.append("")
        elif status == "failed":
            lines.append("_Sin captura de pantalla adjunta para este caso._")
            lines.append("")

        refs = test.get("evidence_refs") or []
        if refs:
            lines.append("### Artefactos")
            lines.append("")
            for ref in refs[:8]:
                if not isinstance(ref, dict):
                    continue
                ref_path = ref.get("screenshot_path") or ref.get("path") or ""
                ref_label = ref.get("label") or ref.get("step_id") or ref_path
                if ref_path:
                    lines.append(f"- `{ref_path}` — {ref_label}")
            lines.append("")
        sections += 1

    if not sections:
        lines.extend(
            [
                "## Sin ejecuciones registradas",
                "",
                "No se encontraron tests ejecutados en el resultado de Playwright.",
                "",
            ]
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    return {
        "path": str(dest),
        "sections": sections,
        "screenshot_count": manifest.get("screenshot_count", 0),
    }
