"""Azure Service Bus implementation (stub when connection string missing)."""
import logging
import os

from src.events.bus import EventBus, EventHandler
from src.events.schemas import PlatformEvent
from src.events.in_memory import InMemoryBus

logger = logging.getLogger("AzureServiceBusBus")


class AzureServiceBusBus(EventBus):
    """Delegates to in-memory when Azure SB is not configured."""

    def __init__(self):
        self._conn = os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING", "")
        self._queue = os.getenv("SERVICE_BUS_QUEUE_NAME", "aptp-events")
        self._fallback = InMemoryBus()
        self._available = bool(self._conn)

    def subscribe(self, handler: EventHandler) -> None:
        self._fallback.subscribe(handler)

    async def publish(self, event: PlatformEvent) -> None:
        if not self._available:
            await self._fallback.publish(event)
            return
        try:
            from azure.servicebus.aio import ServiceBusClient
            from azure.servicebus import ServiceBusMessage
            import json

            async with ServiceBusClient.from_connection_string(self._conn) as client:
                sender = client.get_queue_sender(queue_name=self._queue)
                async with sender:
                    body = json.dumps(event.model_dump(), default=str)
                    await sender.send_messages(ServiceBusMessage(body))
        except Exception as e:
            logger.warning("Service Bus publish failed, using fallback: %s", e)
            await self._fallback.publish(event)


def create_event_bus() -> EventBus:
    if os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING"):
        return AzureServiceBusBus()
    return InMemoryBus()
