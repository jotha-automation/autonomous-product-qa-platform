"""Detect Playwright execution-environment failures vs functional test failures."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_BOX_DRAWING_RE = re.compile(r"[\u2500-\u257f\u2550-\u256c]")
_DOCKER_IMAGE_RE = re.compile(
    r"(?:current|required):\s*(mcr\.microsoft\.com/playwright:[^\s\u2551]+)",
    re.IGNORECASE,
)
_PLAYWRIGHT_VERSION_RE = re.compile(r"Playwright was just updated to\s+([\d.]+)", re.IGNORECASE)

_INFRA_MESSAGE_MARKERS = (
    "browsertype.launch",
    "executable doesn't exist",
    "executable does not exist",
    "please update docker image",
    "mcr.microsoft.com/playwright",
    "playwright_browsers_path",
    "browser has been closed",
    "failed to launch",
    "spawn enoent",
    "npm install failed",
    "cannot find module '@playwright/test'",
    "missing runtime secret",
    "aptp_secret_login_user",
    "timed out after",
)


def strip_box_drawing(text: str) -> str:
    """Remove Playwright CLI box-drawing frames from error text."""
    cleaned = _BOX_DRAWING_RE.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def normalize_failure_message(failure: Dict[str, Any]) -> str:
    err = failure.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("stack") or ""
    else:
        msg = str(err or failure.get("title") or "")
    return strip_box_drawing(str(msg))


def is_infrastructure_failure_message(message: str) -> bool:
    msg = strip_box_drawing(message).casefold()
    if not msg:
        return False
    return any(marker in msg for marker in _INFRA_MESSAGE_MARKERS)


def _parse_docker_images(message: str) -> tuple[Optional[str], Optional[str]]:
    current = required = None
    for match in _DOCKER_IMAGE_RE.finditer(message):
        image = match.group(1).strip()
        prefix = match.group(0).casefold()
        if prefix.startswith("required"):
            required = image
        elif prefix.startswith("current"):
            current = image
    return current, required


def _infra_kind(message: str) -> str:
    msg = message.casefold()
    if "timed out after" in msg:
        return "timeout"
    if "missing runtime secret" in msg or "aptp_secret_login_user" in msg:
        return "missing_secrets"
    if "npm install failed" in msg or "cannot find module" in msg:
        return "dependency_install"
    if "executable doesn't exist" in msg or "executable does not exist" in msg or "update docker image" in msg:
        return "browser_launch"
    if "browsertype.launch" in msg or "failed to launch" in msg:
        return "browser_launch"
    return "environment"


def _remediation_steps(kind: str, *, current_image: Optional[str], required_image: Optional[str]) -> List[str]:
    if kind == "browser_launch" and required_image:
        return [
            f"Update the Playwright Docker base image to `{required_image}` (Dockerfile / docker-compose).",
            "Rebuild and redeploy the API container: `docker compose build api && docker compose up -d api`.",
            "Verify `/health` reports the matching Playwright browser revision before re-running the pipeline.",
            "Re-run the pipeline; functional verdicts are invalid until browsers launch successfully.",
        ]
    if kind == "dependency_install":
        return [
            "Ensure `npm install` succeeds inside the generated Playwright project before `npx playwright test`.",
            "Confirm network access and Node/npm versions in the execution container.",
            "Re-run the pipeline after dependencies install cleanly.",
        ]
    if kind == "timeout":
        return [
            "A Playwright execution phase exceeded its time budget and was terminated.",
            "Check target availability/latency, then raise the relevant budget if the run legitimately "
            "needs longer (APTP_NPM_INSTALL_TIMEOUT_S, APTP_PLAYWRIGHT_LIST_TIMEOUT_S, "
            "APTP_PLAYWRIGHT_TEST_TIMEOUT_S).",
            "Re-run the pipeline after resolving the slow or hung phase.",
        ]
    if kind == "missing_secrets":
        return [
            "Confirm bank-portal demo credentials were written to the generated project `.env` "
            "(APTP_SECRET_LOGIN_USER_CLIENTE, CLAVE, TOKEN, etc.).",
            "Rebuild/restart the API container so the latest secret-sync fix is active, then re-run the pipeline.",
            "If using custom credentials from `test-credentials.xlsx`, verify the Usuarios sheet parses correctly.",
        ]
    return [
        "Fix the Playwright execution environment (browser binaries, Docker image, or npm dependencies).",
        "Re-run the pipeline after the environment health check passes.",
    ]


def detect_execution_infrastructure_blocker(
    execution: Dict[str, Any],
    rca: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return a single infra blocker when failures share an environment root cause."""
    if not execution:
        return None

    rca = rca or {}
    if str(rca.get("failure_category") or "").lower() in {"infra", "environment", "infrastructure"}:
        sample = ""
        failures = execution.get("failures") or []
        if failures:
            sample = normalize_failure_message(failures[0])
        if not sample:
            sample = strip_box_drawing(str(execution.get("stderr") or rca.get("root_cause") or ""))
        if sample and is_infrastructure_failure_message(sample):
            affected = int(execution.get("failed") or 0) or len(failures) or len(execution.get("executed_tests") or [])
            kind = _infra_kind(sample)
            current_image, required_image = _parse_docker_images(sample)
            return {
                "category": "infrastructure",
                "failure_kind": kind,
                "title": "Playwright browser launch blocked (Docker image mismatch)"
                if kind == "browser_launch"
                else "Playwright execution environment blocked",
                "description": sample.split("\n", 1)[0].strip(),
                "remediation_steps": _remediation_steps(kind, current_image=current_image, required_image=required_image),
                "affected_test_count": affected,
                "current_docker_image": current_image,
                "required_docker_image": required_image,
                "release_verdict": "invalid",
            }

    messages: List[str] = []
    for failure in execution.get("failures") or []:
        if isinstance(failure, dict):
            messages.append(normalize_failure_message(failure))
    for test in execution.get("executed_tests") or []:
        if not isinstance(test, dict):
            continue
        err = test.get("error")
        if err:
            messages.append(strip_box_drawing(str(err)))
    stderr = strip_box_drawing(str(execution.get("stderr") or ""))
    if stderr:
        messages.append(stderr)
    top_error = strip_box_drawing(str(execution.get("error") or ""))
    if top_error:
        messages.append(top_error)

    infra_messages = [m for m in messages if m and is_infrastructure_failure_message(m)]
    if not infra_messages:
        return None

    failed_count = int(execution.get("failed") or 0)
    executed_count = len(execution.get("executed_tests") or [])
    affected = failed_count or executed_count or len(infra_messages)
    if failed_count and len(infra_messages) < max(1, failed_count // 2):
        # Mixed infra + functional failures — do not collapse.
        return None

    canonical = infra_messages[0]
    first_line = canonical.split("\n", 1)[0].strip()
    kind = _infra_kind(canonical)
    current_image, required_image = _parse_docker_images(canonical)
    version_match = _PLAYWRIGHT_VERSION_RE.search(canonical)
    required_version = version_match.group(1) if version_match else None

    title = "Playwright execution environment blocked"
    if kind == "browser_launch":
        title = "Playwright browser launch blocked (Docker image mismatch)"
    elif kind == "dependency_install":
        title = "Playwright dependency install failed"

    description = first_line
    if required_image and current_image:
        description = (
            f"{first_line} — container image `{current_image}` does not bundle browsers for "
            f"the installed Playwright version; use `{required_image}`."
        )
    elif required_image:
        description = f"{first_line} — update Docker image to `{required_image}`."

    return {
        "category": "infrastructure",
        "failure_kind": kind,
        "title": title,
        "description": description,
        "remediation_steps": _remediation_steps(
            kind, current_image=current_image, required_image=required_image
        ),
        "affected_test_count": affected,
        "current_docker_image": current_image,
        "required_docker_image": required_image,
        "required_playwright_version": required_version,
        "release_verdict": "invalid",
    }


def consolidate_infrastructure_defects(
    defects: List[Dict[str, Any]],
    infra_blocker: Dict[str, Any],
    *,
    execution: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Replace duplicated per-test infra failures with one infrastructure defect."""
    if not infra_blocker:
        return defects

    kept: List[Dict[str, Any]] = []
    has_infra = False
    for defect in defects:
        if defect.get("category") == "infrastructure":
            if not has_infra:
                kept.append(defect)
                has_infra = True
            continue
        if defect.get("category") in {"readiness", "coverage_gap", "healing_applied"}:
            kept.append(defect)
            continue
        if defect.get("source") == "rca_agent" and str(defect.get("category", "")).lower() in {
            "infra",
            "environment",
            "infrastructure",
        }:
            continue
        if defect.get("source") not in {
            "playwright_execution",
            "automation_infrastructure",
            "execution_environment",
        }:
            kept.append(defect)
            continue
        msg = f"{defect.get('title', '')} {defect.get('description', '')}"
        if is_infrastructure_failure_message(msg):
            continue
        kept.append(defect)

    affected = int(infra_blocker.get("affected_test_count") or 0)
    infra_defect = {
        "id": "DEF-INFRA-001",
        "severity": "critical",
        "category": "infrastructure",
        "title": infra_blocker.get("title", "Execution environment blocked"),
        "description": infra_blocker.get("description", ""),
        "source": "execution_environment",
        "status": "open",
        "owner": "ops",
        "requirement_ids": [],
        "remediation_steps": infra_blocker.get("remediation_steps") or [],
        "affected_tests": affected,
        "failure_kind": infra_blocker.get("failure_kind"),
        "current_docker_image": infra_blocker.get("current_docker_image"),
        "required_docker_image": infra_blocker.get("required_docker_image"),
        "release_verdict": "invalid",
    }
    if not has_infra:
        return [infra_defect, *kept]
    return kept


def apply_infrastructure_execution_semantics(
    execution: Dict[str, Any],
    infra_blocker: Dict[str, Any],
) -> Dict[str, Any]:
    """Reclassify execution counters when the environment never ran functional checks."""
    if not infra_blocker:
        return execution

    affected = int(infra_blocker.get("affected_test_count") or execution.get("failed") or 0)
    updated = dict(execution)
    updated["infrastructure_blocker"] = infra_blocker
    updated["functional_failed"] = 0
    updated["tests_blocked"] = affected
    updated["tests_failed"] = 0
    updated["failed"] = 0
    updated["pass_rate"] = 0.0
    updated["release_verdict"] = "invalid"

    executed_tests = []
    for test in execution.get("executed_tests") or []:
        if not isinstance(test, dict):
            continue
        row = dict(test)
        err = strip_box_drawing(str(row.get("error") or ""))
        if row.get("status") == "failed" and (not err or is_infrastructure_failure_message(err)):
            row["status"] = "blocked"
            row["block_reason"] = "infrastructure"
            row["infra_blocker_id"] = "DEF-INFRA-001"
        executed_tests.append(row)
    updated["executed_tests"] = executed_tests
    return updated
