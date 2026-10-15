"""Technical execution report PDF with test results and embedded screenshots."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import fitz  # pymupdf


def _add_heading(page: fitz.Page, text: str, y: float, size: int = 14) -> float:
    page.insert_text((50, y), text, fontsize=size, fontname="helv")
    return y + size + 8


def _add_text(page: fitz.Page, text: str, y: float, size: int = 10, indent: float = 50) -> float:
    width = page.rect.width - indent - 50
    lines: List[str] = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if fitz.get_text_length(trial, fontname="helv", fontsize=size) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)

    for line in lines:
        if y > page.rect.height - 60:
            break
        page.insert_text((indent, y), line[:500], fontsize=size, fontname="helv")
        y += size + 4
    return y


def build_execution_pdf(
    output_path: Path,
    *,
    run_id: str,
    project_id: str,
    target_url: str,
    exec_result: Dict[str, Any],
    manifest: Dict[str, Any],
    evidence_root: Path,
    max_screenshots: int = 12,
) -> Path:
    """Write a technical execution PDF to output_path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 50.0

    y = _add_heading(page, "APTP — Technical Execution Report", y, size=16)
    y = _add_text(page, f"Run ID: {run_id}", y)
    y = _add_text(page, f"Project: {project_id}", y)
    y = _add_text(page, f"Target URL: {target_url or 'N/A'}", y)
    y = _add_text(page, f"Mode: {exec_result.get('mode', 'unknown')}", y)
    y = _add_text(page, f"Generated: {datetime.now(timezone.utc).isoformat()}", y)
    y += 8

    passed = exec_result.get("passed", 0)
    failed = exec_result.get("failed", 0)
    total = exec_result.get("total", passed + failed)
    pass_rate = exec_result.get("pass_rate", 0)
    duration = exec_result.get("duration_seconds", 0)

    y = _add_heading(page, "Summary", y, size=12)
    y = _add_text(page, f"Passed: {passed}  |  Failed: {failed}  |  Total: {total}", y)
    y = _add_text(page, f"Pass rate: {pass_rate * 100:.1f}%  |  Duration: {duration:.2f}s", y)
    y += 8

    y = _add_heading(page, "Test Cases", y, size=12)
    for test in exec_result.get("executed_tests") or []:
        line = (
            f"{test.get('id', '?')} — {test.get('title', '?')} — "
            f"{test.get('status', '?')} — {test.get('duration_ms', 0)}ms"
        )
        y = _add_text(page, line, y, size=9)
        err = test.get("error")
        if err:
            y = _add_text(page, f"  Error: {err}", y, size=8, indent=60)

    failures = exec_result.get("failures") or []
    if failures:
        y += 6
        y = _add_heading(page, "Failures", y, size=12)
        for failure in failures[:10]:
            err = failure.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            y = _add_text(page, f"{failure.get('title', '?')}: {msg}", y, size=9)

    stderr = exec_result.get("stderr") or ""
    if stderr:
        y += 6
        y = _add_heading(page, "Stderr (excerpt)", y, size=12)
        y = _add_text(page, stderr[:1500], y, size=8)

    entries = manifest.get("entries") or []
    if entries:
        page = doc.new_page(width=595, height=842)
        y = 50.0
        y = _add_heading(page, "Execution Evidence", y, size=14)
        embedded = 0
        for entry in entries:
            if embedded >= max_screenshots:
                break
            rel = entry.get("path", "")
            media_type = entry.get("media_type") or "screenshot"
            artifact_path = evidence_root / rel
            if not artifact_path.is_file():
                continue
            y = _add_text(
                page,
                f"{entry.get('test_id', '?')} / {entry.get('step_id', '?')} — {entry.get('label', '')}",
                y,
                size=9,
            )
            if media_type == "diagnostic_text" or rel.endswith(".txt"):
                try:
                    text_body = artifact_path.read_text(encoding="utf-8", errors="replace")[:4000]
                except OSError:
                    text_body = "(could not read diagnostic text)"
                y = _add_text(page, text_body, y, size=8, indent=60)
                embedded += 1
                continue
            if media_type != "screenshot":
                continue
            try:
                rect = fitz.Rect(50, y, 545, y + 200)
                if y + 220 > page.rect.height - 40:
                    page = doc.new_page(width=595, height=842)
                    y = 50.0
                    rect = fitz.Rect(50, y, 545, y + 200)
                page.insert_image(rect, filename=str(artifact_path))
                y += 220
                embedded += 1
            except Exception:
                y = _add_text(page, f"  (could not embed {rel})", y, size=8)

    footer_page = doc[-1]
    _add_text(
        footer_page,
        f"Evidence files: {len(entries)} | Embedded screenshots: {min(len(entries), max_screenshots)}",
        footer_page.rect.height - 30,
        size=8,
    )

    doc.save(str(output_path))
    doc.close()
    return output_path
