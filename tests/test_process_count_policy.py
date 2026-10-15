"""process_count tracks successful pipeline completions only."""
from src.services.intake_registry import IntakeRegistryService
from src.services.db_models import IntakeRootFolder


def test_processing_state_process_count_is_successful_runs():
    row = IntakeRootFolder(
        id="r1",
        project_id="demo",
        drive_folder_id="drive-1",
        root_name="APTP-demo",
        processed=True,
        process_count=2,
        last_pipeline_status="failed",
    )
    state = IntakeRegistryService.processing_state_from_row(row)
    assert state["process_count"] == 2
    assert state["remaining_runs"] == 1
    assert state["can_reprocess"] is True
    assert state["pipeline_succeeded"] is False


def test_failed_run_does_not_increment_process_count_documented():
    """Failed runs must not consume a slot; increment happens only on completed."""
    row = IntakeRootFolder(
        id="r1",
        project_id="demo",
        drive_folder_id="drive-1",
        root_name="APTP-demo",
        process_count=0,
        last_pipeline_status="failed",
    )
    state = IntakeRegistryService.processing_state_from_row(row)
    assert state["process_count"] == 0
    assert state["remaining_runs"] == 3
