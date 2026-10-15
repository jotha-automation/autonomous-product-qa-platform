from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


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
    project_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    version: str = "1.0"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
