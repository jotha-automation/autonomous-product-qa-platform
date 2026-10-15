"""Duplicate intake_root_folders rows coalesce by drive_folder_id."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.services.intake_registry import IntakeRegistryService


def test_canonical_intake_root_prefers_higher_process_count():
    rows = [
        SimpleNamespace(
            id="a",
            process_count=1,
            processed=True,
            last_synced_at=datetime(2026, 1, 1),
            first_seen_at=datetime(2025, 1, 1),
        ),
        SimpleNamespace(
            id="b",
            process_count=2,
            processed=False,
            last_synced_at=datetime(2026, 2, 1),
            first_seen_at=datetime(2025, 2, 1),
        ),
    ]
    assert IntakeRegistryService._canonical_intake_root(rows).id == "b"


def test_merge_root_metadata_keeps_max_process_count():
    canonical = SimpleNamespace(
        process_count=1,
        processed=False,
        last_file_count=1,
        last_folder_count=1,
        last_synced_at=None,
        project_id="OLD",
        root_name="old",
        last_pipeline_run_id=None,
        last_pipeline_status=None,
        last_successful_run_id=None,
        last_results_drive_folder_id=None,
        project_closed_at=None,
    )
    duplicate = SimpleNamespace(
        process_count=2,
        processed=True,
        last_file_count=3,
        last_folder_count=4,
        last_synced_at=datetime(2026, 7, 1),
        project_id="NEW",
        root_name="new",
        last_pipeline_run_id="run-2",
        last_pipeline_status="failed",
        last_successful_run_id=None,
        last_results_drive_folder_id=None,
        project_closed_at=None,
    )
    IntakeRegistryService._merge_root_metadata(canonical, duplicate)
    assert canonical.process_count == 2
    assert canonical.processed is True
    assert canonical.project_id == "NEW"
    assert canonical.last_pipeline_run_id == "run-2"
