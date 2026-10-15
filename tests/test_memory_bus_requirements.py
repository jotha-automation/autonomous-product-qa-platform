import pytest

from src.agents.memory_bus import RunMemoryBus, _requirements_list


def test_requirements_list_from_agent_block():
    block = {"requirements": [{"id": "REQ-1", "description": "Login"}]}
    assert len(_requirements_list(block)) == 1


def test_requirements_list_from_plain_list():
    rows = [{"id": "REQ-1", "description": "Login"}]
    assert _requirements_list(rows) == rows


@pytest.mark.asyncio
async def test_default_knowledge_query_accepts_requirements_block():
    bus = RunMemoryBus.__new__(RunMemoryBus)
    query = bus._default_knowledge_query(
        {
            "requirements": {"requirements": [{"description": "Hero section"}]},
            "functional_scenarios": [],
        }
    )
    assert "Hero section" in query
