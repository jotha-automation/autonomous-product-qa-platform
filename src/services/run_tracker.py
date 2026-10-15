"""Pipeline run event log for real-time UI streaming."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.memory.enhanced_memory import MemoryTier, get_memory

TERMINAL_EVENT_TYPES = frozenset(
    {
        "run_complete",
        "run_failed",
        "run_cancelled",
        "run_partial",
    }
)

TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})

STAGE_LABELS = {
    "initialized": "Initializing",
    "intake": "Document intake",
    "intake_guardrails": "Intake Guardrails",
    "intake_indexing": "Intake indexing",
    "intake_interpretation": "Intake Interpretation Agent",
    "client_intake_interpretation": "Client Intake Interpreter",
    "test_asset_resolution": "Test Asset Resolver",
    "awaiting_target_url": "Awaiting target URL",
    "readiness": "Readiness Agent",
    "requirements": "Requirements Analyst",
    "coverage": "Coverage Agent",
    "test_design": "Test Design Agent",
    "traceability": "Traceability Agent",
    "risk_assessment": "Risk Assessment Agent",
    "knowledge_retrieval": "Knowledge Retrieval Agent",
    "executive_reporting": "Executive Reporting Agent",
    "playwright_architect": "Playwright Architect",
    "playwright_generator": "Playwright Generator",
    "execution": "Execution Agent",
    "failure_rca": "Failure RCA Agent",
    "self_healing": "Self-Healing Agent",
    "defect_detection": "Defect Detection",
    "excel_deliverable": "Excel Deliverable",
    "reexecution": "Re-execution",
    "cancelled": "Cancelled",
    "completed": "Completed",
    "failed": "Failed",
}

def _events_key(run_id: str) -> str:
    return f"run_events:{run_id}"


def seed_pipeline_status(
    run_id: str,
    *,
    project_id: Optional[str] = None,
    stage: str = "initialized",
    status: str = "running",
) -> None:
    """Write initial pipeline status so stream token / polling work before orchestrator starts."""
    payload: Dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "stage": stage,
    }
    if project_id:
        payload["project_id"] = project_id
    get_memory().write(f"pipeline_status:{run_id}", payload, tier=MemoryTier.SHORT_TERM)


def append_run_event(
    run_id: str,
    event_type: str,
    message: str,
    *,
    stage: Optional[str] = None,
    agent: Optional[str] = None,
    level: str = "info",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    from src.services.run_secret_store import RunSecretStore

    store = RunSecretStore.for_run(run_id)
    safe_message = store.redact_text(message)
    safe_details = store.redact_dict(details or {})

    memory = get_memory()
    events: List[Dict[str, Any]] = memory.read(_events_key(run_id)) or []
    events.append(
        {
            "type": event_type,
            "message": safe_message,
            "stage": stage,
            "agent": agent,
            "level": level,
            "details": safe_details,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    )
    memory.write(_events_key(run_id), events, tier=MemoryTier.SHORT_TERM)


def get_run_events(run_id: str) -> List[Dict[str, Any]]:
    return get_memory().read(_events_key(run_id)) or []


def run_terminal_event_emitted(events: List[Dict[str, Any]]) -> bool:
    return any(event.get("type") in TERMINAL_EVENT_TYPES for event in events)


def partial_run_message(result: Dict[str, Any]) -> str:
    exec_r = (result.get("results") or {}).get("execution") or {}
    passed = int(exec_r.get("passed") or 0)
    failed = int(exec_r.get("failed") or 0)
    total = int(exec_r.get("total") or passed + failed)
    return f"Pipeline finished with test failures ({passed}/{total} passed, {failed} failed)"


def _artifact_urls(run_id: str, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    manifest = manifest or {}
    return {
        "manifest_url": manifest.get("manifest_path") or f"/api/v1/runs/{run_id}/artifacts/manifest",
        "artifacts_url": manifest.get("download_path") or f"/api/v1/runs/{run_id}/artifacts",
        "reports_url": f"/api/v1/runs/{run_id}/reports",
        "visual_report_url": f"/api/v1/runs/{run_id}/visual-report",
        "bundles": manifest.get("bundles") or {},
    }


def deliver_run_artifacts(run_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Persist reports to disk and emit SSE events so the UI can refetch without reload."""
    from src.services.artifact_bundle import get_run_manifest, write_run_reports_to_disk

    artifact_dir = write_run_reports_to_disk(run_id, result)
    if artifact_dir:
        result = {**result, "artifact_dir": str(artifact_dir)}
        get_memory().write(f"pipeline_summary:{run_id}", result, tier=MemoryTier.LONG_TERM)

    manifest: Dict[str, Any] = {}
    try:
        manifest = get_run_manifest(run_id)
    except FileNotFoundError:
        pass

    reports = (result.get("results") or {}).get("reports") or {}
    status = result.get("status") or "failed"
    artifact_urls = _artifact_urls(run_id, manifest)
    append_run_event(
        run_id,
        "artifacts_ready",
        "Reports and artifacts are available",
        stage="completed" if status in {"completed", "partial"} else status,
        level="info" if status == "completed" else "warn",
        details={
            "status": status,
            "partial": status == "partial",
            "report_keys": list(reports.keys()),
            "artifact_dir": result.get("artifact_dir"),
            "tests_passed": manifest.get("tests_passed"),
            "tests_failed": manifest.get("tests_failed"),
            **artifact_urls,
        },
    )

    terminal_details = {**artifact_urls, "status": status}

    if status == "partial":
        append_run_event(
            run_id,
            "run_partial",
            partial_run_message(result),
            stage="completed",
            level="warn",
            details=terminal_details,
        )
    elif status == "completed":
        append_run_event(
            run_id,
            "run_complete",
            "Pipeline finished",
            stage="completed",
            details=terminal_details,
        )
    elif status == "failed":
        append_run_event(
            run_id,
            "run_failed",
            result.get("error") or "Pipeline failed",
            stage="failed",
            level="error",
            details=terminal_details,
        )
    elif status == "cancelled":
        append_run_event(
            run_id,
            "run_cancelled",
            result.get("error") or "Pipeline cancelled",
            stage="cancelled",
            level="warn",
            details=terminal_details,
        )
    return result


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())
