"""Event bus abstraction for APTP."""
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    PROJECT_CREATED = "PROJECT_CREATED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_UPDATED = "DOCUMENT_UPDATED"
    DOCUMENT_DELETED = "DOCUMENT_DELETED"
    PROJECT_READY = "PROJECT_READY"
    PROCESSING_STARTED = "PROCESSING_STARTED"
    PROCESSING_COMPLETED = "PROCESSING_COMPLETED"
    REPORT_GENERATED = "REPORT_GENERATED"
    TEST_PLAN_CREATED = "TEST_PLAN_CREATED"
    PLAYWRIGHT_GENERATED = "PLAYWRIGHT_GENERATED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    TEST_FAILED = "TEST_FAILED"
    RCA_COMPLETED = "RCA_COMPLETED"
    HEALING_STARTED = "HEALING_STARTED"
    HEALING_COMPLETED = "HEALING_COMPLETED"
    REEXECUTION_COMPLETED = "REEXECUTION_COMPLETED"


class PlatformEvent(BaseModel):
    event_type: EventType
    correlation_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0"
    event_id: str = Field(default_factory=lambda: str(uuid4()))


EventHandler = Callable[[PlatformEvent], Any]


class EventBus(ABC):
    @abstractmethod
    async def publish(self, event: PlatformEvent) -> None:
        ...

    @abstractmethod
    async def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        ...


class InMemoryBus(EventBus):
    def __init__(self):
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._history: List[PlatformEvent] = []

    async def publish(self, event: PlatformEvent) -> None:
        self._history.append(event)
        for handler in self._handlers.get(event.event_type, []):
            result = handler(event)
            if hasattr(result, "__await__"):
                await result

    async def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def get_history(self) -> List[PlatformEvent]:
        return list(self._history)


class AzureServiceBusBus(EventBus):
    """Stub for Azure Service Bus — falls back to in-memory when not configured."""

    def __init__(self, connection_string: Optional[str] = None, queue_name: str = "aptp-events"):
        self._fallback = InMemoryBus()
        self._connection_string = connection_string
        self._queue_name = queue_name
        self._available = bool(connection_string)

    async def publish(self, event: PlatformEvent) -> None:
        if not self._available:
            await self._fallback.publish(event)
            return
        # Real implementation would use azure.servicebus.aio.ServiceBusClient
        await self._fallback.publish(event)

    async def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        await self._fallback.subscribe(event_type, handler)


_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        import os

        conn = os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING")
        if conn:
            from src.events.azure_service_bus import AzureServiceBusBus as RealAzureServiceBusBus

            _bus = RealAzureServiceBusBus()
        else:
            _bus = InMemoryBus()
    return _bus
