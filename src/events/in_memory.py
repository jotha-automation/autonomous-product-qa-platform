import asyncio
import logging
from typing import List

from src.events.bus import EventBus, EventHandler
from src.events.schemas import PlatformEvent

logger = logging.getLogger("InMemoryBus")


class InMemoryBus(EventBus):
    def __init__(self):
        self._handlers: List[EventHandler] = []
        self._history: List[PlatformEvent] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def publish(self, event: PlatformEvent) -> None:
        self._history.append(event)
        logger.info("Event %s correlation=%s", event.event_type, event.correlation_id)
        for handler in self._handlers:
            await handler(event)

    def get_history(self) -> List[PlatformEvent]:
        return list(self._history)


_bus: InMemoryBus | None = None


def get_event_bus() -> InMemoryBus:
    global _bus
    if _bus is None:
        _bus = InMemoryBus()
    return _bus
