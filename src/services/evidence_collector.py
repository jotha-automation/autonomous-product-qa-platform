"""Collect execution evidence (screenshots, HTML report) into run-scoped artifact dirs."""
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.reporting.pdf_execution_report import build_execution_pdf
from src.reporting.evidence_markdown_report import build_evidence_markdown_report


def _artifact_root(project_dir: Path, run_id: str) -> Path:
    root = project_dir / "aptp-artifacts" / run_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def _copy_tree(src: Path, dest: Path) -> int:
    if not src.is_dir():
        return 0
    count = 0
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def _scan_project_evidence(project_dir: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    evidence_dir = project_dir / "evidence"
    if not evidence_dir.is_dir():
        return entries

    label_index = _labels_from_results_json(project_dir)
    video_labels = _video_labels_from_results_json(project_dir)

    for png in sorted(evidence_dir.rglob("*.png")):
        rel = png.relative_to(project_dir).as_posix()
        parts = png.relative_to(evidence_dir).parts
        test_key = parts[0] if parts else "unknown"
        step_id = png.stem
        label = step_id
        if test_key in label_index and step_id in label_index[test_key]:
            label = label_index[test_key][step_id]
        entries.append(
            {
                "test_key": test_key,
                "step_id": step_id,
                "label": label,
                "source_path": str(png),
                "relative_path": rel,
                "media_type": "screenshot",
            }
        )

    for webm in sorted(evidence_dir.rglob("*.webm")):
        rel = webm.relative_to(project_dir).as_posix()
        parts = webm.relative_to(evidence_dir).parts
        test_key = parts[0] if parts else "unknown"
        step_id = webm.stem
        video_meta = video_labels.get(test_key) or {}
        entries.append(
            {
                "test_key": test_key,
                "step_id": step_id,
                "label": video_meta.get("label") or "Full test run video",
                "source_path": str(webm),
                "relative_path": rel,
                "media_type": "video",
            }
        )
    return entries


def _safe_segment(value: str) -> str:
    """ASCII-only slug — must match Playwright fixtures/evidence.ts safeSegment."""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value or "step").strip("-")
    return (cleaned[:96] or "step")


def _labels_from_results_json(project_dir: Path) -> Dict[str, Dict[str, str]]:
    """Parse Playwright JSON reporter annotations for evidence step labels."""
    report_path = project_dir / "reports" / "results.json"
    if not report_path.is_file():
        return {}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    labels: Dict[str, Dict[str, str]] = {}

    def walk_suites(suites: List[Dict[str, Any]]) -> None:
        for suite in suites or []:
            for spec in suite.get("specs") or []:
                title = spec.get("title") or ""
                test_key = _safe_segment(title)
                for test in spec.get("tests") or []:
                    for result in test.get("results") or []:
                        for ann in result.get("annotations") or []:
                            if ann.get("type") != "evidence":
                                continue
                            try:
                                payload = json.loads(ann.get("description") or "{}")
                            except json.JSONDecodeError:
                                continue
                            step_id = payload.get("stepId") or payload.get("step_id")
                            label = payload.get("label") or step_id
                            if step_id:
                                labels.setdefault(test_key, {})[str(step_id)] = str(label)
            walk_suites(suite.get("suites") or [])

    walk_suites(report.get("suites") or [])
    return labels


def _video_labels_from_results_json(project_dir: Path) -> Dict[str, Dict[str, str]]:
    """Parse Playwright JSON reporter annotations for per-test video evidence."""
    report_path = project_dir / "reports" / "results.json"
    if not report_path.is_file():
        return {}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    videos: Dict[str, Dict[str, str]] = {}

    def walk_suites(suites: List[Dict[str, Any]]) -> None:
        for suite in suites or []:
            for spec in suite.get("specs") or []:
                title = spec.get("title") or ""
                test_key = _safe_segment(title)
                for test in spec.get("tests") or []:
                    for result in test.get("results") or []:
                        for ann in result.get("annotations") or []:
                            if ann.get("type") != "evidence-video":
                                continue
                            try:
                                payload = json.loads(ann.get("description") or "{}")
                            except json.JSONDecodeError:
                                continue
                            videos[test_key] = {
                                "label": str(payload.get("label") or "Full test run video"),
                                "path": str(payload.get("path") or ""),
                            }
            walk_suites(suite.get("suites") or [])

    walk_suites(report.get("suites") or [])
    return videos


def _create_mock_placeholder(dest: Path, label: str) -> Path:
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (800, 450), color=(30, 41, 59))
        draw = ImageDraw.Draw(img)
        draw.text((24, 24), f"APTP mock evidence\n{label}", fill=(148, 163, 184))
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, format="PNG")
    except Exception:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
    return dest


def collect_and_persist_evidence(
    run_id: str,
    project_dir: Path,
    exec_result: Dict[str, Any],
    *,
    attempt: int = 0,
    project_id: str = "",
    target_url: str = "",
    obligations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Normalize evidence under aptp-artifacts/{run_id}/evidence/ and build PDF.

    This function performs heavy synchronous file I/O; call it from async code
    via ``asyncio.to_thread`` so it does not block the event loop.
    """
    project_dir = Path(project_dir)
    artifact_root = _artifact_root(project_dir, run_id)
    evidence_root = artifact_root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)

    mode = exec_result.get("mode", "live")
    manifest_entries: List[Dict[str, Any]] = []

    if mode == "mock":
        for test in exec_result.get("executed_tests") or []:
            test_id = test.get("id", "TC-000")
            step_id = "step-mock"
            rel = f"screenshots/{test_id}/{step_id}.png"
            dest = evidence_root / rel
            _create_mock_placeholder(dest, f"{test.get('title', test_id)} (mock)")
            manifest_entries.append(
                {
                    "test_id": test_id,
                    "test_title": test.get("title"),
                    "step_id": step_id,
                    "label": "Mock execution placeholder",
                    "path": rel,
                    "media_type": "screenshot",
                    "status": test.get("status"),
                    "attempt": attempt,
                    "mode": "mock",
                }
            )
    else:
        for entry in _scan_project_evidence(project_dir):
            src = Path(entry["source_path"])
            test_key = entry["test_key"]
            step_id = entry["step_id"]
            media_type = entry.get("media_type") or "screenshot"
            if media_type == "video":
                rel = f"videos/{test_key}/{step_id}.webm"
            else:
                rel = f"screenshots/{test_key}/{step_id}.png"
            dest = evidence_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            manifest_entries.append(
                {
                    "test_id": test_key,
                    "test_title": test_key,
                    "step_id": step_id,
                    "label": entry.get("label", step_id),
                    "path": rel,
                    "media_type": media_type,
                    "status": "captured",
                    "attempt": attempt,
                    "mode": mode,
                }
            )

        html_src = project_dir / "reports" / "html"
        if html_src.is_dir():
            _copy_tree(html_src, evidence_root / "html")

        test_results = project_dir / "test-results"
        if test_results.is_dir():
            for png in test_results.rglob("*.png"):
                if "node_modules" in png.parts:
                    continue
                rel_name = png.name
                rel = f"screenshots/failures/{rel_name}"
                dest = evidence_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(png, dest)
                    manifest_entries.append(
                        {
                            "test_id": "failure",
                            "test_title": rel_name,
                            "step_id": rel_name,
                            "label": "Playwright failure screenshot",
                            "path": rel,
                            "media_type": "screenshot",
                            "status": "failed",
                            "attempt": attempt,
                            "mode": mode,
                        }
                    )
            for trace in test_results.rglob("trace.zip"):
                if "node_modules" in trace.parts:
                    continue
                rel_name = trace.name
                test_dir = trace.parent.name
                rel = f"traces/{test_dir}/{rel_name}"
                dest = evidence_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(trace, dest)
                    manifest_entries.append(
                        {
                            "test_id": test_dir,
                            "test_title": test_dir,
                            "step_id": "trace",
                            "label": "Playwright trace",
                            "path": rel,
                            "media_type": "trace",
                            "status": "captured",
                            "attempt": attempt,
                            "mode": mode,
                        }
                    )

        if not manifest_entries:
            failures = exec_result.get("failures") or []
            for idx, failure in enumerate(failures[:12]):
                title = failure.get("title") or f"failure-{idx + 1}"
                message = (failure.get("error") or {}).get("message") or str(failure.get("error") or "")
                category = (failure.get("error") or {}).get("category") or "error"
                safe = re.sub(r"[^\w\-]+", "-", title).strip("-")[:80] or f"failure-{idx + 1}"
                rel = f"artifacts/{category}/{safe}.txt"
                dest = evidence_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(message[:8000], encoding="utf-8")
                manifest_entries.append(
                    {
                        "test_id": title,
                        "test_title": title,
                        "step_id": category,
                        "label": f"{category}: {title}",
                        "path": rel,
                        "media_type": "diagnostic_text",
                        "status": "failed",
                        "attempt": attempt,
                        "mode": mode,
                    }
                )

            stdout = (exec_result.get("stdout") or exec_result.get("stderr") or "")[:8000]
            if stdout:
                rel = "artifacts/compile/stdout.txt"
                dest = evidence_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(stdout, encoding="utf-8")
                manifest_entries.append(
                    {
                        "test_id": "compile",
                        "test_title": "Playwright compile/execution log",
                        "step_id": "stdout",
                        "label": "Execution log excerpt",
                        "path": rel,
                        "media_type": "diagnostic_text",
                        "status": "failed",
                        "attempt": attempt,
                        "mode": mode,
                    }
                )

    compile_errors = int(exec_result.get("compile_errors") or 0)
    if not compile_errors:
        compile_errors = sum(
            1
            for f in exec_result.get("failures") or []
            if isinstance(f, dict)
            and (
                (f.get("error") or {}).get("category") == "compile"
                if isinstance(f.get("error"), dict)
                else "SyntaxError" in str(f.get("error") or "")
            )
        )

    manifest = {
        "run_id": run_id,
        "project_id": project_id,
        "attempt": attempt,
        "mode": mode,
        "execution_status": exec_result.get("status"),
        "tests_passed": int(exec_result.get("passed") or 0),
        "tests_failed": int(exec_result.get("failed") or 0),
        "compile_errors": compile_errors,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "screenshot_count": sum(1 for e in manifest_entries if e.get("media_type") == "screenshot"),
        "video_count": sum(1 for e in manifest_entries if e.get("media_type") == "video"),
        "diagnostic_count": sum(1 for e in manifest_entries if e.get("media_type") == "diagnostic_text"),
        "entries": manifest_entries,
    }
    manifest_path = evidence_root / "evidence-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    pdf_path = artifact_root / "execution-report.pdf"
    build_execution_pdf(
        pdf_path,
        run_id=run_id,
        project_id=project_id,
        target_url=target_url,
        exec_result=exec_result,
        manifest=manifest,
        evidence_root=evidence_root,
        max_screenshots=int(os.getenv("APTP_PDF_MAX_SCREENSHOTS", "12")),
    )

    md_path = artifact_root / "evidence-report.md"
    md_result = build_evidence_markdown_report(
        md_path,
        run_id=run_id,
        project_id=project_id,
        execution_results=exec_result,
        manifest=manifest,
        obligations=obligations or [],
    )

    exec_result["evidence"] = {
        "artifact_dir": str(artifact_root),
        "manifest_path": str(manifest_path),
        "pdf_path": str(pdf_path),
        "markdown_report_path": str(md_path),
        "screenshot_count": manifest["screenshot_count"],
        "video_count": manifest["video_count"],
        "html_report_path": str(evidence_root / "html" / "index.html")
        if (evidence_root / "html" / "index.html").is_file()
        else None,
        "attempt": attempt,
    }
    exec_result["artifacts"] = _normalize_artifact_paths(exec_result.get("artifacts") or {}, manifest_entries)

    _attach_evidence_refs(exec_result, manifest_entries)

    return {
        "artifact_root": str(artifact_root),
        "manifest_path": str(manifest_path),
        "pdf_path": str(pdf_path),
        "markdown_report_path": str(md_path),
        "screenshot_count": manifest["screenshot_count"],
        "video_count": manifest["video_count"],
        "manifest": manifest,
        "markdown_report": md_result,
    }


def _normalize_artifact_paths(
    artifacts: Dict[str, List[str]], manifest_entries: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    normalized: Dict[str, List[str]] = {
        "screenshots": list(artifacts.get("screenshots") or []),
        "traces": list(artifacts.get("traces") or []),
        "videos": list(artifacts.get("videos") or []),
    }
    for entry in manifest_entries:
        path = entry.get("path", "")
        media_type = entry.get("media_type") or "screenshot"
        if not path:
            continue
        if media_type == "video":
            if path not in normalized["videos"]:
                normalized["videos"].append(path)
        elif media_type == "screenshot" and path not in normalized["screenshots"]:
            normalized["screenshots"].append(path)
    return normalized


def _attach_evidence_refs(
    exec_result: Dict[str, Any], manifest_entries: List[Dict[str, Any]]
) -> None:
    refs_by_test: Dict[str, List[Dict[str, str]]] = {}
    for entry in manifest_entries:
        test_id = entry.get("test_id") or "unknown"
        refs_by_test.setdefault(test_id, []).append(
            {
                "step_id": entry.get("step_id", ""),
                "screenshot_path": entry.get("path", ""),
                "media_type": entry.get("media_type", "screenshot"),
                "label": entry.get("label", ""),
            }
        )

    for test in exec_result.get("executed_tests") or []:
        title = test.get("title") or ""
        test_id = test.get("id") or ""
        evidence_key = test.get("evidence_key") or _safe_segment(title)
        matched = (
            refs_by_test.get(evidence_key)
            or refs_by_test.get(test_id)
            or refs_by_test.get(title)
            or []
        )
        if not matched:
            for key, refs in refs_by_test.items():
                if (
                    test_id in key
                    or title in key
                    or key in title
                    or evidence_key in key
                    or key in evidence_key
                ):
                    matched = refs
                    break
        test["evidence_refs"] = matched
