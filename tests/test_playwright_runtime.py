import json

import pytest

from src.playwright_version import PLAYWRIGHT_VERSION
from src.services.playwright_runtime import (
    check_npm_playwright_version,
    npm_playwright_version_mismatch_message,
    stderr_indicates_browser_mismatch,
)


def test_npm_version_mismatch_message():
    msg = npm_playwright_version_mismatch_message("1.40.0")
    assert msg
    assert "1.40.0" in msg
    assert PLAYWRIGHT_VERSION in msg


def test_check_npm_playwright_version_detects_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    monkeypatch.setenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    monkeypatch.setenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "false")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    pkg_dir = tmp_path / "node_modules" / "@playwright" / "test"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "@playwright/test", "version": "1.40.0"}),
        encoding="utf-8",
    )

    err = check_npm_playwright_version(tmp_path)
    assert err
    assert "1.40.0" in err


def test_check_npm_playwright_version_allows_match(tmp_path, monkeypatch):
    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    monkeypatch.setenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    pkg_dir = tmp_path / "node_modules" / "@playwright" / "test"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "@playwright/test", "version": PLAYWRIGHT_VERSION}),
        encoding="utf-8",
    )

    assert check_npm_playwright_version(tmp_path) is None


def test_stderr_indicates_browser_mismatch():
    assert stderr_indicates_browser_mismatch("Executable doesn't exist at /ms-playwright/chromium-123")
    assert not stderr_indicates_browser_mismatch("Timeout 30000ms exceeded")


def test_ensure_playwright_browsers_inside_asyncio(tmp_path, monkeypatch):
    """Startup validation runs inside FastAPI lifespan (asyncio loop) — must not fail spuriously."""
    import asyncio

    from src.runtime_policy import RealDataRequiredError
    from src.services.playwright_runtime import ensure_playwright_browsers

    browsers = tmp_path / "ms-playwright"
    shell_dir = browsers / "chromium_headless_shell-1228" / "chrome-linux"
    shell_dir.mkdir(parents=True)
    (shell_dir / "headless_shell").write_text("", encoding="utf-8")

    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    monkeypatch.setenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    monkeypatch.setenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "false")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers))

    async def _run() -> None:
        ensure_playwright_browsers()

    asyncio.run(_run())


def test_ensure_playwright_browsers_raises_when_path_missing(monkeypatch):
    from src.runtime_policy import RealDataRequiredError
    from src.services.playwright_runtime import ensure_playwright_browsers

    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    monkeypatch.setenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent-browsers-path")

    with pytest.raises(RealDataRequiredError, match="No Chromium browsers found"):
        ensure_playwright_browsers()
