"""SQLAlchemy models for Product + Automation domains."""
import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    String, Text, DateTime, Float, Integer, ForeignKey, JSON, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None  # type: ignore


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    drive_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[str] = mapped_column(String(50))
    readiness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    doc_type: Mapped[str] = mapped_column(String(20))
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    canonical_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    external_id: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    if Vector is not None:
        embedding = mapped_column(Vector(1536), nullable=True)
    else:
        embedding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class UseCase(Base):
    __tablename__ = "use_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class BusinessRule(Base):
    __tablename__ = "business_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    rule_text: Mapped[str] = mapped_column(Text)


class AcceptanceCriterion(Base):
    __tablename__ = "acceptance_criteria"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    criterion_text: Mapped[str] = mapped_column(Text)


class FunctionalTestCase(Base):
    __tablename__ = "functional_test_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(255))
    scenario_json: Mapped[dict] = mapped_column(JSON, default=dict)


class CoverageResult(Base):
    __tablename__ = "coverage_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36))
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class TraceabilityRow(Base):
    __tablename__ = "traceability_rows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36))
    requirement_id: Mapped[str] = mapped_column(String(50))
    test_id: Mapped[str] = mapped_column(String(50))
    coverage: Mapped[str] = mapped_column(String(20))


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36))
    assessment_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36))
    report_type: Mapped[str] = mapped_column(String(50))
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TestSuite(Base):
    __tablename__ = "test_suites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    feature: Mapped[str] = mapped_column(String(255))
    suite_json: Mapped[dict] = mapped_column(JSON, default=dict)


class GeneratedTest(Base):
    __tablename__ = "generated_tests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"))
    file_path: Mapped[str] = mapped_column(String(512))


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(50))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pass_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)


class RCAReport(Base):
    __tablename__ = "rca_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36))
    root_cause: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict)


class HealingAction(Base):
    __tablename__ = "healing_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36))
    test_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    change_applied: Mapped[str] = mapped_column(Text)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(50), default="initialized")
    status: Mapped[str] = mapped_column(String(50), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    results_json: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeChunk(Base):
    """Semantic index for requirements, scenarios, defects, RCA, healing (pgvector)."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    chunk_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    if Vector is not None:
        from src.knowledge.embeddings import embedding_dimensions

        embedding = mapped_column(Vector(embedding_dimensions()), nullable=True)
    else:
        embedding: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
