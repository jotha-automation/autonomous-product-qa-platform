"""Redis fail-fast and health reporting."""
from __future__ import annotations

import pytest

from src.memory.enhanced_memory import EnhancedSharedMemory
from src.runtime_policy import RedisRequiredError, ensure_redis_available, require_redis


def test_redis_fallback_when_not_required(monkeypatch):
    monkeypatch.delenv("APTP_REQUIRE_REDIS", raising=False)
    mem = EnhancedSharedMemory(redis_url="redis://127.0.0.1:1")
    assert not mem.is_redis_available()
    assert mem.redis_health()["status"] == "unavailable"


def test_redis_required_raises_on_connection_failure(monkeypatch):
    monkeypatch.setenv("APTP_REQUIRE_REDIS", "true")
    with pytest.raises(RedisRequiredError):
        EnhancedSharedMemory(redis_url="redis://127.0.0.1:1")


def test_ensure_redis_available_raises_when_required_missing(monkeypatch):
    monkeypatch.setenv("APTP_REQUIRE_REDIS", "true")
    mem = EnhancedSharedMemory.__new__(EnhancedSharedMemory)
    mem._available = False
    with pytest.raises(RedisRequiredError):
        ensure_redis_available(mem)


def test_redis_health_required_missing(monkeypatch):
    monkeypatch.setenv("APTP_REQUIRE_REDIS", "true")
    mem = EnhancedSharedMemory.__new__(EnhancedSharedMemory)
    mem._available = False
    mem._redis_error = "connection refused"
    health = mem.redis_health()
    assert health["status"] == "required_missing"
    assert require_redis() is True
