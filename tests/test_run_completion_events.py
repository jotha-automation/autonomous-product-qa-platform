"""SSE completion handshake: artifacts_ready + terminal events."""
import json
from pathlib import Path

import pytest

from src.memory.enhanced_memory import get_memory
from src.services.run_tracker import (
    TERMINAL_EVENT_TYPES,
    deliver_run_artifacts,
    get_run_events,
    run_terminal_event_emitted,
)


@pytest.fixture
def run_id():
    return "run-completion-handshake"


@pytest.fixture
def partial_result(run_id, tmp_path, monkeypatch):
    project_dir = tmp_path / "DEMO-QA"
    artifact_dir = project_dir / "aptp-artifacts" / run_id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "deliverable.xlsx").write_bytes(b"fake-xlsx")

    from src.services import artifact_bundle

    monkeypatch.setattr(artifact_bundle, "GENERATED_PROJECTS_ROOT", tmp_path)

    return {
        "run_id": run_id,
        "project_id": "DEMO-QA",
        "status": "partial",
        "stage": "completed",
        "results": {
            "reports": {
                "executive": {"executive_summary": "Summary"},
                "automation": {"execution": {"passed": 0, "failed": 32, "total": 32}},
            },
            "execution": {"passed": 0, "failed": 32, "total": 32, "status": "failed"},
        },
    }


def test_deliver_run_artifacts_emits_handshake(partial_result, run_id):
    from src.memory.enhanced_memory import MemoryTier

    get_memory().write(f"run_events:{run_id}", [], tier=MemoryTier.SHORT_TERM)

    deliver_run_artifacts(run_id, partial_result)
    events = get_run_events(run_id)
    types = [e["type"] for e in events]

    assert "artifacts_ready" in types
    assert "run_partial" in types
    assert run_terminal_event_emitted(events)

    artifacts_event = next(e for e in events if e["type"] == "artifacts_ready")
    assert artifacts_event["details"]["manifest_url"].endswith("/artifacts/manifest")
    assert artifacts_event["details"]["reports_url"].endswith("/reports")
    assert artifacts_event["details"]["visual_report_url"].endswith("/visual-report")

    terminal = next(e for e in events if e["type"] == "run_partial")
    assert terminal["details"]["manifest_url"] == artifacts_event["details"]["manifest_url"]


def test_run_terminal_event_emitted_anywhere_in_log():
    events = [
        {"type": "stage", "message": "Running"},
        {"type": "artifacts_ready", "message": "ready"},
        {"type": "run_partial", "message": "done"},
    ]
    assert run_terminal_event_emitted(events)
    assert "run_partial" in TERMINAL_EVENT_TYPES


def test_manifest_written_on_deliver(partial_result, run_id, tmp_path, monkeypatch):
    from src.services import artifact_bundle

    monkeypatch.setattr(artifact_bundle, "GENERATED_PROJECTS_ROOT", tmp_path)
    deliver_run_artifacts(run_id, partial_result)

    manifest_path = tmp_path / "DEMO-QA" / "aptp-artifacts" / run_id / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["tests_failed"] == 32
