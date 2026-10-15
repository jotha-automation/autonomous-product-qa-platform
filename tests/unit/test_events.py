import pytest

from src.events.bus import EventType, InMemoryBus, PlatformEvent


@pytest.mark.asyncio
async def test_in_memory_bus_publish():
    bus = InMemoryBus()
    received = []

    async def handler(event: PlatformEvent):
        received.append(event)

    await bus.subscribe(EventType.PROCESSING_STARTED, handler)
    await bus.publish(PlatformEvent(event_type=EventType.PROCESSING_STARTED, correlation_id="run-1", payload={"phase": 1}))
    assert len(received) == 1
    assert received[0].correlation_id == "run-1"


def test_release_readiness_score():
    from src.reporting.scores import compute_release_readiness_score
    score = compute_release_readiness_score(
        {"coverage_score": 0.9},
        {"release_risk_score": 0.1},
        [{"coverage": "full"}, {"coverage": "full"}],
        {"score": 1.0},
    )
    assert 0 < score <= 100


def test_release_readiness_boosts_on_clean_execution():
    from src.reporting.scores import compute_release_readiness_score

    score = compute_release_readiness_score(
        {"coverage_score": 0.0},
        {"release_risk_score": 0.9},
        [],
        {"score": 0.0},
        execution={"total": 56, "failed": 0, "pass_rate": 1.0},
    )
    assert score >= 70.0
