from pathlib import Path

from src.services.run_history import (
    count_publishable_artifact_files,
    has_published_reports_file,
    is_unscheduled_probe_run,
    merge_run_histories,
    run_looks_like_unscheduled_probe,
)


def test_count_publishable_artifact_files_excludes_internal_metadata(tmp_path: Path):
    run_dir = tmp_path / "run-a"
    run_dir.mkdir()
    (run_dir / "pipeline_summary.json").write_text("{}", encoding="utf-8")
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (run_dir / "reports.json").write_text("{}", encoding="utf-8")

    assert count_publishable_artifact_files(run_dir) == 1
    assert has_published_reports_file(run_dir / "reports.json") is False


def test_has_published_reports_file_detects_bundle(tmp_path: Path):
    reports = tmp_path / "reports.json"
    reports.write_text('{"executive": {"summary": "ok"}}', encoding="utf-8")
    assert has_published_reports_file(reports) is True


def test_merge_run_histories_prefers_richer_entries():
    db = [{"run_id": "run-a", "status": "failed", "source": "platform", "file_count": 0}]
    local = [
        {
            "run_id": "run-a",
            "status": "failed",
            "source": "local",
            "file_count": 3,
            "has_reports": True,
        }
    ]
    merged = merge_run_histories(db, local)
    assert len(merged) == 1
    assert merged[0]["file_count"] == 3
    assert merged[0]["has_reports"] is True


def test_merge_run_histories_sorts_by_completed_at_desc():
    runs = merge_run_histories(
        [
            {
                "run_id": "run-older",
                "status": "failed",
                "completed_at": "2026-06-15T10:00:00+00:00",
            }
        ],
        [
            {
                "run_id": "run-newer",
                "status": "completed",
                "completed_at": "2026-06-17T01:18:00+00:00",
            }
        ],
    )
    assert runs[0]["run_id"] == "run-newer"


def test_is_unscheduled_probe_run_detects_watcher_pause():
    summary = {
        "status": "awaiting_input",
        "stage": "awaiting_target_url",
        "awaiting": "target_url",
    }
    assert is_unscheduled_probe_run(summary) is True
    assert is_unscheduled_probe_run({"status": "completed", "stage": "done"}) is False


def test_run_looks_like_unscheduled_probe_from_artifact_dir(tmp_path: Path):
    run_dir = tmp_path / "run-probe"
    run_dir.mkdir()
    (run_dir / "pipeline_summary.json").write_text(
        '{"status":"awaiting_input","stage":"awaiting_target_url"}',
        encoding="utf-8",
    )
    assert run_looks_like_unscheduled_probe({"artifact_dir": str(run_dir)}) is True
    assert run_looks_like_unscheduled_probe({"status": "awaiting_input", "has_reports": False}) is True
    assert run_looks_like_unscheduled_probe({"status": "completed", "has_reports": True}) is False
