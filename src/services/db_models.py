"""SQLAlchemy models for APTP domain."""
import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, List, Optional

from sqlalchemy import (
    JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None  # type: ignore


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class ProjectStatus(str, PyEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DocumentType(str, PyEnum):
    BRD = "BRD"
    PRD = "PRD"
    UC = "UC"
    AC = "AC"
    OTHER = "OTHER"


class RunStatus(str, PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    drive_folder_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    releases: Mapped[List["Release"]] = relationship(back_populates="project")
    documents: Mapped[List["Document"]] = relationship(back_populates="project")


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[str] = mapped_column(String(64))
    readiness_score: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    project: Mapped["Project"] = relationship(back_populates="releases")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    doc_type: Mapped[str] = mapped_column(String(16))
    source_uri: Mapped[Optional[str]] = mapped_column(String(512))
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    canonical_json: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    project: Mapped["Project"] = relationship(back_populates="documents")


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[Optional[str]] = mapped_column(String(32))
    embedding = mapped_column(Vector(1536) if Vector else JSON, nullable=True)  # type: ignore


class UseCase(Base):
    __tablename__ = "use_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text)
    actors: Mapped[Optional[list]] = mapped_column(JSON)


class BusinessRule(Base):
    __tablename__ = "business_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    rule: Mapped[str] = mapped_column(Text)


class AcceptanceCriteria(Base):
    __tablename__ = "acceptance_criteria"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    requirement_id: Mapped[Optional[str]] = mapped_column(String(64))


class FunctionalTestCase(Base):
    __tablename__ = "functional_test_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    steps: Mapped[Optional[list]] = mapped_column(JSON)
    requirement_ids: Mapped[Optional[list]] = mapped_column(JSON)


class CoverageResult(Base):
    __tablename__ = "coverage_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str] = mapped_column(String(64))
    gaps: Mapped[Optional[list]] = mapped_column(JSON)
    duplicates: Mapped[Optional[list]] = mapped_column(JSON)
    conflicts: Mapped[Optional[list]] = mapped_column(JSON)
    coverage_score: Mapped[Optional[float]] = mapped_column(Float)


class TraceabilityRow(Base):
    __tablename__ = "traceability_rows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str] = mapped_column(String(64))
    requirement_id: Mapped[str] = mapped_column(String(64))
    test_id: Mapped[str] = mapped_column(String(64))
    coverage: Mapped[str] = mapped_column(String(32))


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str] = mapped_column(String(64))
    risk_scores: Mapped[Optional[dict]] = mapped_column(JSON)
    release_risk: Mapped[Optional[str]] = mapped_column(String(32))
    release_risk_score: Mapped[Optional[float]] = mapped_column(Float)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str] = mapped_column(String(64))
    report_type: Mapped[str] = mapped_column(String(64))
    content: Mapped[Optional[dict]] = mapped_column(JSON)
    format: Mapped[str] = mapped_column(String(16), default="json")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TestSuite(Base):
    __tablename__ = "test_suites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    suite_id: Mapped[str] = mapped_column(String(64))
    feature: Mapped[str] = mapped_column(String(255))
    plan_json: Mapped[Optional[dict]] = mapped_column(JSON)


class GeneratedTest(Base):
    __tablename__ = "generated_tests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"))
    test_id: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(512))


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    pass_rate: Mapped[Optional[float]] = mapped_column(Float)
    results_json: Mapped[Optional[dict]] = mapped_column(JSON)


class RCAReport(Base):
    __tablename__ = "rca_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_run_id: Mapped[str] = mapped_column(ForeignKey("execution_runs.id"))
    root_cause: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    report_json: Mapped[Optional[dict]] = mapped_column(JSON)


class HealingAction(Base):
    __tablename__ = "healing_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid
    )
    test_id: Mapped[str] = mapped_column(String(64))
    execution_run_id: Mapped[str] = mapped_column(ForeignKey("execution_runs.id"))
    change_applied: Mapped[Optional[dict]] = mapped_column(JSON)
    success: Mapped[bool] = mapped_column(default=False)


class IntakeSystemRoot(Base):
    """Single configured Drive folder (e.g. APTP) — parent of all project folders."""

    __tablename__ = "intake_system_roots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    drive_folder_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    root_name: Mapped[str] = mapped_column(String(64), default="APTP")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    project_folder_count: Mapped[int] = mapped_column(Integer, default=0)


class IntakeRootFolder(Base):
    """Registered Drive/local root folder for a QA project."""

    __tablename__ = "intake_root_folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    drive_folder_id: Mapped[str] = mapped_column(String(255), index=True)
    root_name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(16), default="drive")
    system_root_drive_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    folder_slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_file_count: Mapped[int] = mapped_column(Integer, default=0)
    last_folder_count: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[bool] = mapped_column(default=False)
    process_count: Mapped[int] = mapped_column(Integer, default=0)
    last_pipeline_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_pipeline_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_successful_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_results_drive_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class IntakeSubfolder(Base):
    """Discovered subfolder within a project intake tree."""

    __tablename__ = "intake_subfolders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    root_id: Mapped[str] = mapped_column(ForeignKey("intake_root_folders.id"), index=True)
    relative_path: Mapped[str] = mapped_column(String(512))
    folder_name: Mapped[str] = mapped_column(String(255))
    drive_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IntakeFileRecord(Base):
    """Indexed file snapshot for change detection."""

    __tablename__ = "intake_file_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    root_id: Mapped[str] = mapped_column(ForeignKey("intake_root_folders.id"), index=True)
    drive_file_id: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(String(512), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    inferred_doc_type: Mapped[str] = mapped_column(String(16), default="OTHER")
    content_fingerprint: Mapped[str] = mapped_column(String(255))
    modified_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="indexed")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class PipelineRunRecord(Base):
    """Persisted pipeline run history (independent of Drive artifact upload)."""

    __tablename__ = "pipeline_run_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    drive_folder_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    artifact_dir: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    drive_uploaded: Mapped[bool] = mapped_column(default=False)
    drive_run_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class IntakeChangeLog(Base):
    """Audit log of folder/file lifecycle events."""

    __tablename__ = "intake_change_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    root_id: Mapped[str] = mapped_column(ForeignKey("intake_root_folders.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_path: Mapped[str] = mapped_column(String(512))
    entity_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_fingerprint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_fingerprint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
