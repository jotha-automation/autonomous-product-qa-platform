"""Drive manager treats partial pipeline runs as deliverable completions."""


def _pipeline_has_deliverables(status: str) -> bool:
    return status in ("completed", "partial")


def partial_run_message(result: dict) -> str:
    exec_r = (result.get("results") or {}).get("execution") or {}
    passed = int(exec_r.get("passed") or 0)
    failed = int(exec_r.get("failed") or 0)
    total = int(exec_r.get("total") or passed + failed)
    return f"Pipeline finished with test failures ({passed}/{total} passed, {failed} failed)"


def test_pipeline_has_deliverables_includes_partial():
    assert _pipeline_has_deliverables("completed") is True
    assert _pipeline_has_deliverables("partial") is True
    assert _pipeline_has_deliverables("failed") is False
    assert _pipeline_has_deliverables("awaiting_input") is False


def test_partial_run_message_includes_counts():
    msg = partial_run_message(
        {"results": {"execution": {"passed": 0, "failed": 3, "total": 3}}}
    )
    assert "0/3 passed" in msg
    assert "3 failed" in msg
