"""Run completion SSE events and artifact delivery."""
from src.memory.enhanced_memory import get_memory
from src.services.run_tracker import deliver_run_artifacts, get_run_events, partial_run_message


def test_deliver_run_artifacts_emits_partial_and_artifacts_ready(tmp_path, monkeypatch):
    run_id = "run-delivery01"
    project_id = "DELIVERY"
    (tmp_path / "generated-test-projects" / project_id).mkdir(parents=True)

    summary = {
        "run_id": run_id,
        "project_id": project_id,
        "status": "partial",
        "results": {
            "execution": {"passed": 0, "failed": 3, "total": 3, "status": "failed", "mode": "live"},
            "reports": {"qa": {"type": "qa"}, "automation": {"type": "automation"}},
        },
    }

    store: dict = {}

    def _read(key):
        return store.get(key)

    def _write(key, value, tier=None):
        store[key] = value

    monkeypatch.setattr(
        "src.services.artifact_bundle.GENERATED_PROJECTS_ROOT",
        tmp_path / "generated-test-projects",
    )
    monkeypatch.setattr(get_memory(), "read", _read)
    monkeypatch.setattr(get_memory(), "write", _write)

    result = deliver_run_artifacts(run_id, summary)
    events = get_run_events(run_id)
    types = [e["type"] for e in events]

    assert "artifacts_ready" in types
    assert "run_partial" in types
    assert result.get("artifact_dir")


def test_partial_run_message_counts():
    msg = partial_run_message({"results": {"execution": {"passed": 0, "failed": 32, "total": 32}}})
    assert "0/32 passed" in msg
    assert "32 failed" in msg
