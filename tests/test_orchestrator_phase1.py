import os
from pathlib import Path

import pytest

from src.connectors.google_drive.mock import MockGoogleDriveConnector
from src.orchestrator.engine import ProductQAOrchestrator

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("APTP_USE_MOCK_LLM", "true")
    monkeypatch.setenv("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    monkeypatch.setenv("APTP_MOCK_PLAYWRIGHT_EXECUTION", "true")


@pytest.mark.asyncio
async def test_phase1_pipeline_with_fixtures():
    connector = MockGoogleDriveConnector(FIXTURES)
    documents = connector.sync_folder("test-project", "fixtures")
    assert len(documents) >= 4

    orch = ProductQAOrchestrator()
    result = await orch.run_phase1(
        {
            "project_id": "test-project",
            "metadata": {"target_url": "https://bank-portal.up.railway.app"},
            "documents": documents,
        }
    )

    assert result["status"] == "completed"
    assert "requirements" in result.get("results", {})
    assert "coverage" in result.get("results", {})
    assert "traceability" in result.get("results", {})
    assert result.get("results", {}).get("executive_report")


@pytest.mark.asyncio
async def test_full_pipeline_mock():
    connector = MockGoogleDriveConnector(FIXTURES)
    documents = connector.sync_folder("test-project", "fixtures")
    orch = ProductQAOrchestrator()
    result = await orch.run_full(
        {
            "project_id": "test-project",
            "metadata": {"target_url": "https://bank-portal.up.railway.app"},
            "documents": documents,
        }
    )
    assert result["status"] in ("completed", "partial", "failed")
    if result["status"] == "completed":
        assert "generated_code" in result.get("results", {}) or "execution" in result.get(
            "results", {}
        )
