import pytest

from src.connectors.google_drive.system_root import should_auto_trigger_pipeline


class _FakeRegistry:
    def __init__(self, state):
        self._state = state

    async def get_processing_state(self, _drive_folder_id: str):
        return self._state


@pytest.mark.asyncio
async def test_should_auto_trigger_pipeline_respects_draft_state():
    registry = _FakeRegistry({"processed": False, "process_count": 0})
    assert await should_auto_trigger_pipeline(registry, "folder-id", {}) is False


@pytest.mark.asyncio
async def test_should_auto_trigger_pipeline_after_successful_run():
    registry = _FakeRegistry({"processed": True, "process_count": 1})
    assert await should_auto_trigger_pipeline(registry, "folder-id", {}) is True


@pytest.mark.asyncio
async def test_should_auto_trigger_pipeline_manifest_override():
    registry = _FakeRegistry({"processed": False, "process_count": 0})
    assert await should_auto_trigger_pipeline(registry, "folder-id", {"auto_pipeline": True}) is True
    assert await should_auto_trigger_pipeline(registry, "folder-id", {"auto_pipeline": False}) is False
