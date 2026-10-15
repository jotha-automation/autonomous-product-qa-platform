"""Concurrent run scope isolation via contextvars."""
from __future__ import annotations

import asyncio

import pytest

from src.agents.base import BaseAgent
from src.memory.enhanced_memory import EnhancedSharedMemory, MemoryTier
from src.agents.run_scope import (
    bind_memory_scope,
    get_memory_scope,
    reset_memory_scope,
)


class _ScopeProbeAgent(BaseAgent):
    async def execute(self, context):
        await asyncio.sleep(0.05)
        return {"scope": get_memory_scope(), "run_id": context.get("run_id")}


@pytest.mark.asyncio
async def test_concurrent_memory_scopes_do_not_leak():
    memory = EnhancedSharedMemory(redis_url="redis://127.0.0.1:1")
    agent = _ScopeProbeAgent(memory=memory)

    async def run_scoped(run_id: str) -> dict:
        token = bind_memory_scope(f"run:{run_id}")
        try:
            result = await agent.execute({"run_id": run_id})
            written_key = f"run:{run_id}:probe"
            agent.write_memory("probe", {"run_id": run_id}, tier=MemoryTier.WORKING)
            read_back = memory.read(written_key)
            result["read_back"] = read_back
            return result
        finally:
            reset_memory_scope(token)

    results = await asyncio.gather(
        run_scoped("run-alpha"),
        run_scoped("run-beta"),
    )
    by_run = {r["run_id"]: r for r in results}

    assert by_run["run-alpha"]["scope"] == "run:run-alpha"
    assert by_run["run-beta"]["scope"] == "run:run-beta"
    assert by_run["run-alpha"]["read_back"]["run_id"] == "run-alpha"
    assert by_run["run-beta"]["read_back"]["run_id"] == "run-beta"
    assert get_memory_scope() is None


@pytest.mark.asyncio
async def test_shared_agent_instances_keep_distinct_scopes():
    memory = EnhancedSharedMemory(redis_url="redis://127.0.0.1:1")
    shared = _ScopeProbeAgent(memory=memory)

    async def scoped_read(run_id: str) -> str:
        token = bind_memory_scope(f"run:{run_id}")
        try:
            out = await shared.execute({"run_id": run_id})
            return out["scope"]
        finally:
            reset_memory_scope(token)

    alpha, beta = await asyncio.gather(scoped_read("a"), scoped_read("b"))
    assert alpha == "run:a"
    assert beta == "run:b"
