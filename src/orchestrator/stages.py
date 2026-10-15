"""Pipeline stages for APTP v3."""
from enum import Enum


class PipelineStage(str, Enum):
    INITIALIZED = "initialized"
    INTAKE = "intake"
    INTAKE_GUARDRAILS = "intake_guardrails"
    INTAKE_INDEXING = "intake_indexing"
    INTAKE_INTERPRETATION = "intake_interpretation"
    CLIENT_INTAKE_INTERPRETATION = "client_intake_interpretation"
    TEST_ASSET_RESOLUTION = "test_asset_resolution"
    READINESS = "readiness"
    REQUIREMENTS = "requirements"
    COVERAGE = "coverage"
    TEST_DESIGN = "test_design"
    TRACEABILITY = "traceability"
    RISK_ASSESSMENT = "risk_assessment"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    EXECUTIVE_REPORTING = "executive_reporting"
    PLAYWRIGHT_ARCHITECT = "playwright_architect"
    PLAYWRIGHT_GENERATOR = "playwright_generator"
    PLAYWRIGHT_VALIDATION = "playwright_validation"
    EXECUTION = "execution"
    FAILURE_RCA = "failure_rca"
    SELF_HEALING = "self_healing"
    DEFECT_DETECTION = "defect_detection"
    EXCEL_DELIVERABLE = "excel_deliverable"
    REEXECUTION = "reexecution"
    AWAITING_TARGET_URL = "awaiting_target_url"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


PHASE1_STAGES = [
    PipelineStage.READINESS,
    PipelineStage.REQUIREMENTS,
    PipelineStage.COVERAGE,
    PipelineStage.TEST_DESIGN,
    PipelineStage.TRACEABILITY,
    PipelineStage.RISK_ASSESSMENT,
    PipelineStage.KNOWLEDGE_RETRIEVAL,
    PipelineStage.EXECUTIVE_REPORTING,
]

AUTOMATION_STAGES = [
    PipelineStage.PLAYWRIGHT_ARCHITECT,
    PipelineStage.PLAYWRIGHT_GENERATOR,
    PipelineStage.PLAYWRIGHT_VALIDATION,
    PipelineStage.EXECUTION,
    PipelineStage.FAILURE_RCA,
    PipelineStage.SELF_HEALING,
    PipelineStage.REEXECUTION,
]

# Canonical order for resume-from-failure (must match orchestrator execution order).
RESUME_STAGE_ORDER = [
    PipelineStage.INTAKE_INTERPRETATION,
    PipelineStage.CLIENT_INTAKE_INTERPRETATION,
    PipelineStage.TEST_ASSET_RESOLUTION,
    PipelineStage.READINESS,
    PipelineStage.REQUIREMENTS,
    PipelineStage.COVERAGE,
    PipelineStage.TEST_DESIGN,
    PipelineStage.TRACEABILITY,
    PipelineStage.RISK_ASSESSMENT,
    PipelineStage.KNOWLEDGE_RETRIEVAL,
    PipelineStage.PLAYWRIGHT_ARCHITECT,
    PipelineStage.PLAYWRIGHT_GENERATOR,
    PipelineStage.PLAYWRIGHT_VALIDATION,
    PipelineStage.EXECUTION,
    PipelineStage.FAILURE_RCA,
    PipelineStage.SELF_HEALING,
    PipelineStage.REEXECUTION,
    PipelineStage.DEFECT_DETECTION,
    PipelineStage.EXCEL_DELIVERABLE,
    PipelineStage.EXECUTIVE_REPORTING,
]

AUTOMATION_RESUME_STAGES = {
    PipelineStage.PLAYWRIGHT_ARCHITECT,
    PipelineStage.PLAYWRIGHT_GENERATOR,
    PipelineStage.PLAYWRIGHT_VALIDATION,
    PipelineStage.EXECUTION,
    PipelineStage.FAILURE_RCA,
    PipelineStage.SELF_HEALING,
    PipelineStage.REEXECUTION,
    PipelineStage.DEFECT_DETECTION,
    PipelineStage.EXCEL_DELIVERABLE,
    PipelineStage.EXECUTIVE_REPORTING,
}


def normalize_resume_stage(stage: str) -> str:
    """Map heal-loop substages back to execution for resume comparisons."""
    aliases = {
        PipelineStage.FAILURE_RCA.value: PipelineStage.EXECUTION.value,
        PipelineStage.SELF_HEALING.value: PipelineStage.EXECUTION.value,
        PipelineStage.REEXECUTION.value: PipelineStage.EXECUTION.value,
    }
    return aliases.get(stage, stage)


def resume_stage_index(stage: str) -> int:
    normalized = normalize_resume_stage(stage)
    values = [s.value for s in RESUME_STAGE_ORDER]
    if normalized not in values:
        return 0
    return values.index(normalized)


def should_run_resume_stage(resume_from: str | None, stage: str) -> bool:
    if not resume_from:
        return True
    return resume_stage_index(stage) >= resume_stage_index(resume_from)
