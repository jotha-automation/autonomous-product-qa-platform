"""Playwright container/runtime probes — version alignment and browser availability."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.playwright_version import BROWSER_MISMATCH_MARKERS, PLAYWRIGHT_DOCKER_IMAGE, PLAYWRIGHT_VERSION

_CHROMIUM_GLOB_PATTERNS = (
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-linux64/chrome",
    "chromium-*/chrome-linux/headless_shell",
    "chromium-*/chrome-linux64/headless_shell",
    "chromium_headless_shell-*/chrome-linux/headless_shell",
    "chromium_headless_shell-*/chrome-linux64/headless_shell",
)


def browsers_path() -> str:
    return (os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "").strip()


def chromium_binary_present(browsers_dir: str | Path) -> bool:
    base = Path(browsers_dir)
    if not base.is_dir():
        return False
    try:
        return any(
            match
            for pattern in _CHROMIUM_GLOB_PATTERNS
            for match in base.glob(pattern)
        )
    except OSError:
        return False


def _chromium_executable_via_python_sync() -> Tuple[Optional[str], bool]:
    """Probe Playwright's Chromium path via the sync API (must not run inside a running event loop)."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            executable = pw.chromium.executable_path
            ok = bool(executable and os.path.isfile(executable))
            return executable, ok
    except Exception:
        return None, False


def chromium_executable_via_python() -> Tuple[Optional[str], bool]:
    """Return Chromium executable path; runs the sync probe in a thread when called from async code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _chromium_executable_via_python_sync()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_chromium_executable_via_python_sync).result(timeout=60)


def browsers_available() -> bool:
    """True when pre-installed browsers are usable (Docker image or local cache)."""
    path = browsers_path()
    if path and chromium_binary_present(path):
        return True
    _, ok = chromium_executable_via_python()
    return ok


def read_resolved_npm_playwright_version(project_dir: Path) -> Optional[str]:
    for rel in (
        "node_modules/@playwright/test/package.json",
        "node_modules/playwright/package.json",
    ):
        pkg_path = project_dir / rel
        if not pkg_path.is_file():
            continue
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
            version = (data.get("version") or "").strip()
            if version:
                return version
        except (json.JSONDecodeError, OSError):
            continue
    return None


def npm_playwright_version_mismatch_message(resolved: Optional[str]) -> Optional[str]:
    if not resolved or resolved == PLAYWRIGHT_VERSION:
        return None
    return (
        f"@playwright/test@{resolved} was installed but this platform expects {PLAYWRIGHT_VERSION}. "
        f"When PLAYWRIGHT_BROWSERS_PATH is set, npm and pre-installed browsers must match. "
        f"Rebuild the API image ({PLAYWRIGHT_DOCKER_IMAGE}) with `docker compose build --no-cache api`."
    )


def check_npm_playwright_version(project_dir: Path) -> Optional[str]:
    """Return an actionable error when npm resolved a different @playwright/test version."""
    from src.runtime_policy import skip_playwright_install, use_mock_playwright_execution

    if use_mock_playwright_execution() or not skip_playwright_install():
        return None
    if not browsers_path():
        return None
    return npm_playwright_version_mismatch_message(read_resolved_npm_playwright_version(project_dir))


def stderr_indicates_browser_mismatch(stderr: str) -> bool:
    text = stderr or ""
    return any(marker in text for marker in BROWSER_MISMATCH_MARKERS)


def browser_mismatch_actionable_message(stderr: str = "", *, resolved_npm: Optional[str] = None) -> str:
    version_msg = npm_playwright_version_mismatch_message(resolved_npm)
    if version_msg:
        return version_msg
    detail = (stderr or "").strip().split("\n", 1)[0][:300]
    base = (
        f"Playwright browsers are missing or incompatible with {PLAYWRIGHT_VERSION}. "
        f"Rebuild the API image ({PLAYWRIGHT_DOCKER_IMAGE}) or run "
        "`npx playwright install chromium` in the generated project."
    )
    return f"{base} ({detail})" if detail else base


def ensure_playwright_browsers() -> None:
    """Raise when live mode skips install but container browsers are missing."""
    from src.runtime_policy import RealDataRequiredError, skip_playwright_install, use_mock_playwright_execution

    if use_mock_playwright_execution() or not skip_playwright_install():
        return

    path = browsers_path()
    if not path:
        raise RealDataRequiredError(
            "APTP_SKIP_PLAYWRIGHT_INSTALL=true requires PLAYWRIGHT_BROWSERS_PATH "
            f"(e.g. /ms-playwright on Playwright Docker image v{PLAYWRIGHT_VERSION}-jammy)."
        )
    if not chromium_binary_present(path):
        raise RealDataRequiredError(
            f"No Chromium browsers found under PLAYWRIGHT_BROWSERS_PATH={path}. "
            f"Rebuild the API image (`docker compose build --no-cache api`) so browsers match Playwright {PLAYWRIGHT_VERSION}."
        )
    # Pre-installed Docker browsers are validated by filesystem glob; skip sync_playwright here
    # because sync_playwright cannot run inside FastAPI's asyncio lifespan event loop.


def playwright_health_info() -> Dict[str, Any]:
    from src.runtime_policy import skip_playwright_install, use_mock_playwright_execution

    if use_mock_playwright_execution():
        return {
            "live_enabled": False,
            "status": "mock",
            "version": PLAYWRIGHT_VERSION,
            "browsers_available": True,
            "skip_install": skip_playwright_install(),
        }

    path = browsers_path()
    exec_path, py_ok = chromium_executable_via_python()
    preinstalled = bool(path and chromium_binary_present(path))
    browsers_ok = preinstalled or py_ok
    npx_path = shutil.which("npx")

    live_enabled = not use_mock_playwright_execution() and bool(npx_path)
    if browsers_ok:
        status = "ok"
    elif npx_path:
        status = "degraded"
    else:
        status = "missing"

    return {
        "live_enabled": live_enabled,
        "status": status,
        "version": PLAYWRIGHT_VERSION,
        "browsers_path": path,
        "browsers_preinstalled": preinstalled,
        "chromium_executable": exec_path or "",
        "browsers_available": browsers_ok,
        "npx": npx_path,
        "skip_install": skip_playwright_install(),
    }
