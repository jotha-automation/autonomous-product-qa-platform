"""Sanitize Playwright error output and classify infrastructure failures."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.reporting.infra_failures import (
    apply_infrastructure_execution_semantics,
    consolidate_infrastructure_defects,
    detect_execution_infrastructure_blocker,
    is_infrastructure_failure_message,
    normalize_failure_message,
    strip_box_drawing,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ILLEGAL_CONTROL_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text or "")


def strip_box_art(text: str) -> str:
    return strip_box_drawing(text)


def sanitize_playwright_error(text: str, *, max_len: int = 500) -> str:
    """Strip ANSI, box-drawing banners, and control chars from Playwright errors."""
    cleaned = strip_box_drawing(strip_ansi(text or ""))
    cleaned = _ILLEGAL_CONTROL_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned


def is_infrastructure_failure(
    message: str = "",
    *,
    stderr: str = "",
    rca_category: str = "",
    failures: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    category = (rca_category or "").lower()
    if category in {"infra", "environment", "infrastructure"}:
        return True
    blob = f"{message}\n{stderr}"
    if is_infrastructure_failure_message(blob):
        return True
    if failures:
        msgs = [sanitize_playwright_error(normalize_failure_message(f), max_len=300) for f in failures[:5]]
        infra_hits = sum(1 for m in msgs if m and is_infrastructure_failure_message(m))
        return infra_hits > 0 and infra_hits == len([m for m in msgs if m])
    return False


def failures_share_root_cause(failures: List[Dict[str, Any]]) -> bool:
    if len(failures) < 2:
        return bool(failures)
    roots = {
        sanitize_playwright_error(normalize_failure_message(f), max_len=200)
        for f in failures
    }
    roots.discard("")
    return len(roots) == 1


def infrastructure_failure_summary(
    message: str,
    *,
    affected_count: int = 0,
    rca: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rca = rca or {}
    execution = {"failures": [{"error": {"message": message}}], "failed": affected_count}
    blocker = detect_execution_infrastructure_blocker(execution)
    if blocker:
        return {
            "title": blocker.get("title", "Automation environment not ready"),
            "description": blocker.get("description", ""),
            "remediation": blocker.get("remediation_steps") or [],
            "affected_tests_count": blocker.get("affected_test_count", affected_count),
            "affected_tests_sample": (
                (rca.get("failure_summary") or {}).get("affected_tests", [])[:8]
                if isinstance(rca.get("failure_summary"), dict)
                else []
            ),
            "docker_image_current": blocker.get("current_docker_image"),
            "docker_image_required": blocker.get("required_docker_image"),
            "category": "infrastructure",
            "severity": "critical",
            "failure_kind": blocker.get("failure_kind"),
        }

    clean = sanitize_playwright_error(message, max_len=400)
    return {
        "title": "Automation environment not ready",
        "description": clean or "Playwright could not launch a browser in the execution environment.",
        "remediation": ["Align Playwright browsers with the Docker execution image.", "Re-run the pipeline."],
        "affected_tests_count": affected_count,
        "affected_tests_sample": [],
        "docker_image_current": None,
        "docker_image_required": None,
        "category": "infrastructure",
        "severity": "critical",
        "failure_kind": "environment",
    }


def consolidate_execution_failures(
    failures: List[Dict[str, Any]],
    *,
    rca: Optional[Dict[str, Any]] = None,
    execution: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return per-test failures and an optional single infrastructure blocker defect."""
    if not failures:
        return [], None

    execution = dict(execution or {})
    execution["failures"] = failures
    blocker = detect_execution_infrastructure_blocker(execution)
    if not blocker and rca:
        if str(rca.get("failure_category") or "").lower() in {"infra", "environment", "infrastructure"}:
            sample = normalize_failure_message(failures[0]) if failures else ""
            blocker = detect_execution_infrastructure_blocker(
                {**execution, "stderr": sample or execution.get("stderr", "")}
            )

    if not blocker:
        return failures, None

    infra_defect = {
        "severity": "critical",
        "category": "infrastructure",
        "title": blocker.get("title", "Execution environment blocked"),
        "description": blocker.get("description", ""),
        "remediation": blocker.get("remediation_steps") or [],
        "remediation_steps": blocker.get("remediation_steps") or [],
        "source": "automation_infrastructure",
        "status": "open",
        "owner": "ops",
        "affected_tests_count": blocker.get("affected_test_count", len(failures)),
        "affected_tests": blocker.get("affected_test_count", len(failures)),
        "current_docker_image": blocker.get("current_docker_image"),
        "required_docker_image": blocker.get("required_docker_image"),
        "required_playwright_version": blocker.get("required_playwright_version"),
        "failure_kind": blocker.get("failure_kind"),
        "release_verdict": blocker.get("release_verdict", "invalid"),
        "requirement_ids": [],
    }
    return [], infra_defect


def validation_blocked_context(
    execution: Dict[str, Any],
    rca: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    blocker = detect_execution_infrastructure_blocker(execution or {})
    if not blocker and rca:
        if str(rca.get("failure_category") or "").lower() in {"infra", "environment", "infrastructure"}:
            blocker = detect_execution_infrastructure_blocker(execution or {})
    blocked = bool(blocker)
    return {
        "validation_blocked": blocked,
        "validation_status": "blocked" if blocked else ("failed" if execution.get("failed") else "passed"),
        "blocker_reason": "infrastructure" if blocked else None,
        "infrastructure_blocker": blocker,
    }
