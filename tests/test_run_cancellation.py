"""Tests for pipeline cancellation."""
from src.memory.enhanced_memory import MemoryTier, get_memory
from src.services.run_cancellation import (
    RunCancelledError,
    clear_cancel,
    ensure_not_cancelled,
    is_cancelled,
    request_cancel,
)


def test_request_cancel_marks_run_and_events():
    run_id = "run-cancel-test"
    memory = get_memory()
    memory.write(
        f"pipeline_status:{run_id}",
        {"run_id": run_id, "status": "running", "stage": "playwright_generator"},
        tier=MemoryTier.SHORT_TERM,
    )
    memory.write(f"run_events:{run_id}", [], tier=MemoryTier.SHORT_TERM)

    result = request_cancel(run_id, get_status=lambda rid: memory.read(f"pipeline_status:{rid}"))

    assert result["status"] == "cancelled"
    assert result["cancelled_at_stage"] == "playwright_generator"
    assert is_cancelled(run_id)
    status = memory.read(f"pipeline_status:{run_id}")
    assert status["status"] == "cancelled"
    events = memory.read(f"run_events:{run_id}")
    assert any(e.get("type") == "run_cancelled" for e in events)

    clear_cancel(run_id)


def test_request_cancel_rejects_terminal_run():
    run_id = "run-cancel-done"
    memory = get_memory()
    memory.write(
        f"pipeline_status:{run_id}",
        {"run_id": run_id, "status": "completed", "stage": "completed"},
        tier=MemoryTier.SHORT_TERM,
    )

    try:
        request_cancel(run_id, get_status=lambda rid: memory.read(f"pipeline_status:{rid}"))
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "already completed" in str(exc)


def test_ensure_not_cancelled_raises():
    run_id = "run-cancel-flag"
    memory = get_memory()
    memory.write(f"run_cancel:{run_id}", True, tier=MemoryTier.SHORT_TERM)
    try:
        ensure_not_cancelled(run_id)
        assert False, "expected RunCancelledError"
    except RunCancelledError:
        pass
    finally:
        clear_cancel(run_id)
