from src.services.intake_registry import IntakeRegistryService
from src.services.db_models import IntakeRootFolder
from datetime import datetime


def test_processing_state_closed_blocks_reprocess():
    row = IntakeRootFolder(
        id="r1",
        project_id="demo",
        drive_folder_id="drive-1",
        root_name="APTP-demo",
        processed=True,
        process_count=1,
        last_pipeline_status="completed",
        last_successful_run_id="run-abc",
        project_closed_at=datetime.utcnow(),
    )
    state = IntakeRegistryService.processing_state_from_row(row)
    assert state["pipeline_succeeded"] is True
    assert state["can_reprocess"] is False
    assert state["project_closed"] is True
    assert state["last_successful_run_id"] == "run-abc"


def test_processing_state_allows_retries_until_limit():
    row = IntakeRootFolder(
        id="r1",
        project_id="demo",
        drive_folder_id="drive-1",
        root_name="APTP-demo",
        processed=True,
        process_count=2,
        last_pipeline_status="completed",
        last_successful_run_id="run-ok",
    )
    state = IntakeRegistryService.processing_state_from_row(row)
    assert state["can_reprocess"] is True
    assert state["remaining_runs"] == 1


def test_processing_state_at_limit_blocks_even_if_succeeded():
    row = IntakeRootFolder(
        id="r1",
        project_id="demo",
        drive_folder_id="drive-1",
        root_name="APTP-demo",
        processed=True,
        process_count=3,
        last_pipeline_status="completed",
        last_successful_run_id="run-ok",
    )
    state = IntakeRegistryService.processing_state_from_row(row)
    assert state["pipeline_succeeded"] is True
    assert state["can_reprocess"] is False
    assert state["remaining_runs"] == 0
