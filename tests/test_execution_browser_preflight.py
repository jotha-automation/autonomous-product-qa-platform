import json

import pytest

from src.agents.execution import ExecutionAgent


@pytest.mark.asyncio
async def test_run_playwright_fails_fast_on_npm_version_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("APTP_ALLOW_MOCKS", "false")
    monkeypatch.setenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    monkeypatch.setenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "false")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    project = tmp_path / "pw-project"
    project.mkdir()
    (project / "package.json").write_text(
        json.dumps({"name": "test", "devDependencies": {"@playwright/test": "1.40.0"}}),
        encoding="utf-8",
    )
    pkg_dir = project / "node_modules" / "@playwright" / "test"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": "@playwright/test", "version": "1.40.0"}),
        encoding="utf-8",
    )

    async def fake_communicate(self):
        return b"", b""

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_subprocess(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)

    agent = ExecutionAgent.__new__(ExecutionAgent)
    result = await agent._run_playwright(project, "https://example.com", run_id=None)

    assert result.get("infra_error") is True
    assert result.get("passed") == 0
    assert "1.40.0" in (result.get("error") or "")
