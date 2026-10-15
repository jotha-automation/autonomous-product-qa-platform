"""Product QA Orchestrator — Fase 1 + Automation Nivel 3."""
import asyncio
import logging
import os
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.intake_guardrails import IntakeGuardrailsAgent
from src.agents.intake_interpretation import IntakeInterpretationAgent
from src.agents.client_intake_interpreter import ClientIntakeInterpreterAgent
from src.agents.test_asset_resolver import TestAssetResolverAgent
from src.agents.qa_obligation_extractor import QaObligationExtractorAgent
from src.agents.excel_deliverable import ExcelDeliverableAgent
from src.agents.readiness import ReadinessAgent
from src.agents.requirements_analyst import RequirementsAnalystAgent
from src.agents.coverage import CoverageAgent
from src.agents.site_exploration import SiteExplorationAgent
from src.agents.test_design import TestDesignAgent
from src.agents.traceability import TraceabilityAgent
from src.agents.risk_assessment import RiskAssessmentAgent
from src.agents.knowledge_retrieval import KnowledgeRetrievalAgent
from src.agents.executive_reporting import ExecutiveReportingAgent
from src.agents.playwright_architect import PlaywrightArchitectAgent
from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.agents.playwright_validation import PlaywrightValidationAgent
from src.agents.execution import ExecutionAgent
from src.agents.failure_rca import FailureRCAAgent
from src.agents.self_healing import SelfHealingAgent
from src.agents.defect_detection import DefectDetectionAgent
from src.events.bus import EventType, PlatformEvent, get_event_bus
from src.intake.metadata import MetadataExtractor
from src.intake.parser import DocumentParser
from src.memory.enhanced_memory import EnhancedSharedMemory, MemoryTier, get_memory
from src.agents.run_scope import (
    bind_memory_scope,
    bind_pipeline_ctx,
    get_pipeline_ctx,
    reset_memory_scope,
    reset_pipeline_ctx,
)
from src.orchestrator.stages import PipelineStage, should_run_resume_stage
from src.reporting.builders import build_all_reports
from src.agents.memory_bus import bus_for_run
from src.services.artifact_bundle import snapshot_generated_scripts, write_run_reports_to_disk
from src.services.evidence_collector import collect_and_persist_evidence
from src.services.run_tracker import append_run_event, deliver_run_artifacts, stage_label
from src.services.run_cancellation import RunCancelledError, clear_cancel, ensure_not_cancelled

logger = logging.getLogger("ProductQAOrchestrator")


@dataclass
class PipelineContext:
    run_id: str
    project_id: str
    mode: str = "full"
    stage: PipelineStage = PipelineStage.INITIALIZED
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    results: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    documents: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ProductQAOrchestrator:
    def __init__(self, max_retries: int = 2, retry_delay: float = 0.5):
        self.memory = get_memory()
        self.bus = get_event_bus()
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_heal_attempts = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))
        self.parser = DocumentParser()
        self.metadata_extractor = MetadataExtractor()

        self.readiness = ReadinessAgent(self.memory)
        self.intake_guardrails = IntakeGuardrailsAgent(self.memory)
        self.intake_interpreter = IntakeInterpretationAgent(self.memory)
        self.client_intake_interpreter = ClientIntakeInterpreterAgent(self.memory)
        self.test_asset_resolver = TestAssetResolverAgent(self.memory)
        self.qa_obligation_extractor = QaObligationExtractorAgent(self.memory)
        self.excel_deliverable = ExcelDeliverableAgent(self.memory)
        self.requirements = RequirementsAnalystAgent(self.memory)
        self.coverage = CoverageAgent(self.memory)
        self.site_exploration = SiteExplorationAgent(self.memory)
        self.test_design = TestDesignAgent(self.memory)
        self.traceability = TraceabilityAgent(self.memory)
        self.risk = RiskAssessmentAgent(self.memory)
        self.knowledge = KnowledgeRetrievalAgent(self.memory)
        self.executive = ExecutiveReportingAgent(self.memory)
        self.pw_architect = PlaywrightArchitectAgent(self.memory)
        self.pw_generator = PlaywrightGeneratorAgent(self.memory)
        self.pw_validation = PlaywrightValidationAgent(self.memory)
        self.execution = ExecutionAgent(self.memory)
        self.rca = FailureRCAAgent(self.memory)
        self.healing = SelfHealingAgent(self.memory)
        self.defect_detection = DefectDetectionAgent(self.memory)

    def _all_agents(self):
        return [
            self.readiness, self.intake_guardrails, self.intake_interpreter, self.client_intake_interpreter,
            self.test_asset_resolver, self.qa_obligation_extractor, self.excel_deliverable,
            self.requirements, self.coverage, self.site_exploration, self.test_design,
            self.traceability, self.risk, self.knowledge, self.executive,
            self.pw_architect, self.pw_generator, self.pw_validation, self.execution, self.rca, self.healing,
            self.defect_detection,
        ]

    def _run_scope(self, run_id: str) -> str:
        return f"run:{run_id}"

    def _bind_run_scope(self, run_id: str):
        return bind_memory_scope(self._run_scope(run_id))

    def _reset_run_scope(self, scope_token) -> None:
        reset_memory_scope(scope_token)

    def _bind_pipeline_ctx(self, ctx: PipelineContext):
        return bind_pipeline_ctx(ctx)

    def _reset_pipeline_ctx(self, ctx_token) -> None:
        reset_pipeline_ctx(ctx_token)

    def _write_status(self, ctx: PipelineContext, *, status_override: Optional[str] = None) -> None:
        ensure_not_cancelled(ctx.run_id)
        if status_override:
            run_status = status_override
        elif ctx.stage == PipelineStage.FAILED:
            run_status = "failed"
        elif ctx.stage == PipelineStage.CANCELLED:
            run_status = "cancelled"
        elif ctx.stage == PipelineStage.COMPLETED:
            run_status = "completed"
        elif ctx.stage == PipelineStage.AWAITING_TARGET_URL:
            run_status = "awaiting_input"
        else:
            run_status = "running"
        payload = {
            "run_id": ctx.run_id,
            "stage": ctx.stage.value,
            "stage_label": stage_label(ctx.stage.value),
            "project_id": ctx.project_id,
            "status": run_status,
            "results_keys": list(ctx.results.keys()),
            "errors": ctx.errors,
        }
        if ctx.stage == PipelineStage.AWAITING_TARGET_URL:
            payload["awaiting"] = "target_url"
        self.memory.write(f"pipeline_status:{ctx.run_id}", payload, tier=MemoryTier.SHORT_TERM)
        append_run_event(
            ctx.run_id,
            "stage",
            stage_label(ctx.stage.value),
            stage=ctx.stage.value,
        )

    async def _emit(self, event_type: EventType, run_id: str, payload: Dict) -> None:
        from src.services.run_secret_store import RunSecretStore

        redacted = RunSecretStore.for_run(run_id).redact_dict(payload)
        await self.bus.publish(PlatformEvent(event_type=event_type, correlation_id=run_id, payload=redacted))

    async def _execute_agent(self, name: str, agent, context: Dict) -> Any:
        ctx = get_pipeline_ctx()
        if ctx:
            ensure_not_cancelled(ctx.run_id)
            append_run_event(
                ctx.run_id,
                "agent_start",
                f"Running {name}",
                stage=ctx.stage.value,
                agent=name,
            )
        for attempt in range(self.max_retries):
            try:
                result = await agent.execute(context)
                if ctx:
                    ensure_not_cancelled(ctx.run_id)
                    append_run_event(
                        ctx.run_id,
                        "agent_complete",
                        f"{name} completed",
                        stage=ctx.stage.value,
                        agent=name,
                    )
                return result
            except Exception as e:
                logger.error("%s failed attempt %d: %s", name, attempt + 1, e)
                if ctx and attempt >= self.max_retries - 1:
                    append_run_event(
                        ctx.run_id,
                        "agent_error",
                        str(e),
                        stage=ctx.stage.value,
                        agent=name,
                        level="error",
                    )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))
                else:
                    raise

    async def _execute_agents_parallel(self, agent_configs: List[tuple], ctx: PipelineContext) -> List[Any]:
        """Execute multiple agents in parallel with shared cancellation check.

        Args:
            agent_configs: List of (name, agent, context) tuples
            ctx: Current pipeline context for cancellation checks

        Returns:
            List of results in same order as agent_configs
        """
        async def run_single(name: str, agent, context: Dict) -> Any:
            return await self._execute_agent(name, agent, context)

        tasks = [run_single(name, agent, context) for name, agent, context in agent_configs]
        return await asyncio.gather(*tasks, return_exceptions=True)

    def _check_parallel_results(self, results: List[Any], agent_names: List[str], ctx: PipelineContext) -> None:
        """Check parallel execution results and raise on first exception."""
        for idx, (name, result) in enumerate(zip(agent_names, results)):
            if isinstance(result, Exception):
                logger.error("Parallel agent %s failed: %s", name, result)
                raise result

    async def _pause_for_target_url(
        self,
        ctx: PipelineContext,
        payload: Dict[str, Any],
        *,
        skip_artifact_persist: bool = False,
    ) -> Dict[str, Any]:
        from src.services.pipeline_checkpoint import save_checkpoint

        save_checkpoint(
            ctx.run_id,
            {
                "awaiting": "target_url",
                "run_id": ctx.run_id,
                "project_id": ctx.project_id,
                "mode": payload.get("mode", "full"),
                "metadata": dict(ctx.metadata or {}),
                "drive_folder_id": payload.get("drive_folder_id"),
                "partial_results": dict(ctx.results),
                "start_time": ctx.start_time.isoformat(),
                "skip_artifact_persist": skip_artifact_persist,
            },
        )
        ctx.stage = PipelineStage.AWAITING_TARGET_URL
        self._write_status(ctx)
        append_run_event(
            ctx.run_id,
            "awaiting_input",
            "Target URL not found in intake documents. Pipeline paused until operator provides the application URL.",
            stage=PipelineStage.AWAITING_TARGET_URL.value,
            level="warn",
            details={"awaiting": "target_url"},
        )
        return await self._finalize(ctx, awaiting_input=True)

    async def run_phase1(self, payload: Dict[str, Any], *, skip_artifact_persist: bool = False) -> Dict[str, Any]:
        resume_from = payload.get("resume_from")
        ctx, scope_token = await self._init_context(payload)
        ctx.mode = payload.get("mode", "phase1")
        if payload.get("start_time"):
            ctx.start_time = datetime.fromisoformat(payload["start_time"])
        partial_results = payload.get("partial_results") or {}
        if partial_results:
            ctx.results.update(partial_results)
        resume_from_stage = resume_from if resume_from and resume_from != "intake_interpretation" else None
        ctx_token = self._bind_pipeline_ctx(ctx)
        skip_intake_prefix = resume_from == "intake_interpretation"

        if skip_intake_prefix:
            from src.services.pipeline_checkpoint import clear_checkpoint, load_checkpoint

            checkpoint = load_checkpoint(ctx.run_id)
            if not checkpoint or checkpoint.get("awaiting") != "target_url":
                raise ValueError(f"Run {ctx.run_id} is not paused awaiting target URL")
            ctx.results = dict(checkpoint.get("partial_results") or {})
            ctx.start_time = datetime.fromisoformat(checkpoint["start_time"])
            if checkpoint.get("metadata"):
                ctx.metadata = {**checkpoint["metadata"], **ctx.metadata}
            from src.services.run_hints import merge_hints_into_metadata

            ctx.metadata = merge_hints_into_metadata(ctx.metadata, ctx.run_id)
            clear_checkpoint(ctx.run_id)
            skip_artifact_persist = checkpoint.get("skip_artifact_persist", skip_artifact_persist)
            append_run_event(
                ctx.run_id,
                "run_resumed",
                f"Pipeline resumed with operator target URL: {ctx.metadata.get('target_url', '')}",
                stage=PipelineStage.INTAKE_INTERPRETATION.value,
            )
        else:
            append_run_event(ctx.run_id, "run_started", "Pipeline started", stage=ctx.stage.value)
            await self._emit(EventType.PROCESSING_STARTED, ctx.run_id, {"phase": 1})

        try:
            from src.intake.intake_mode import detect_intake_profile
            from src.services.target_url import intake_has_target_url

            profile = detect_intake_profile(ctx.documents, ctx.metadata or {})
            intake_mode = profile.legacy_mode
            ctx.metadata["intake_mode"] = intake_mode
            ctx.metadata["intake_profile"] = profile.to_dict()
            interpreted_intake: Dict[str, Any] = {}

            if not skip_intake_prefix:
                ctx.stage = PipelineStage.INTAKE_GUARDRAILS
                self._write_status(ctx)
                guardrails = await self._execute_agent(
                    "IntakeGuardrails",
                    self.intake_guardrails,
                    {
                        "run_id": ctx.run_id,
                        "project_id": ctx.project_id,
                        "documents": ctx.documents,
                        "metadata": ctx.metadata,
                    },
                )
                ctx.results["intake_guardrails"] = guardrails
                if not guardrails.get("accepted"):
                    ctx.errors.append(
                        {
                            "error": guardrails.get("message", "Intake guardrails rejected project"),
                            "guardrails": guardrails.get("guardrails"),
                        }
                    )
                    ctx.stage = PipelineStage.COMPLETED
                    return await self._finalize(ctx, partial=True)

                ctx.stage = PipelineStage.INTAKE_INDEXING
                self._write_status(ctx)
                from src.intake.intake_index import build_intake_index

                index = build_intake_index(ctx.project_id, ctx.documents)
                ctx.results["intake_index"] = {"file_count": len(index.entries)}
                self.memory.write(
                    self._run_scope(ctx.run_id) + ":intake_index",
                    index.list_files(),
                    tier=MemoryTier.WORKING,
                )

                if not intake_has_target_url(ctx.documents, ctx.metadata, ctx.run_id):
                    return await self._pause_for_target_url(ctx, payload, skip_artifact_persist=skip_artifact_persist)

            ctx.stage = PipelineStage.INTAKE_INTERPRETATION
            self._write_status(ctx)
            from src.intake.metadata import enrich_metadata_from_documents
            from src.intake.normalization import normalize_intake_documents

            normalized, norm_meta = await normalize_intake_documents(
                ctx.documents,
                self.intake_interpreter,
                ctx.metadata,
                execute_agent=lambda agent, context: self._execute_agent(
                    agent.__class__.__name__, agent, {**context, "run_id": ctx.run_id, "project_id": ctx.project_id}
                ),
                memory=self.memory,
                run_id=ctx.run_id,
            )
            ctx.documents = normalized
            ctx.metadata = enrich_metadata_from_documents(ctx.metadata, normalized)
            for key in ("target_url", "project_id", "release"):
                if norm_meta.get(key):
                    ctx.metadata[key] = norm_meta[key]
            ctx.results["intake_interpretation"] = norm_meta
            interpreted_intake = norm_meta.get("interpreted_intake") or {}

            if interpreted_intake:
                ctx.stage = PipelineStage.CLIENT_INTAKE_INTERPRETATION
                self._write_status(ctx)
                refined = await self._execute_agent(
                    "QaObligationExtractor",
                    self.qa_obligation_extractor,
                    {
                        "run_id": ctx.run_id,
                        "project_id": ctx.project_id,
                        "documents": ctx.documents,
                        "interpreted_intake": interpreted_intake,
                    },
                )
                interpreted_intake = refined.get("interpreted_intake") or interpreted_intake

                ctx.stage = PipelineStage.TEST_ASSET_RESOLUTION
                self._write_status(ctx)
                resolved = await self._execute_agent(
                    "TestAssetResolver",
                    self.test_asset_resolver,
                    {
                        "run_id": ctx.run_id,
                        "project_id": ctx.project_id,
                        "documents": ctx.documents,
                        "interpreted_intake": interpreted_intake,
                    },
                )
                interpreted_intake = resolved.get("interpreted_intake") or interpreted_intake
                ctx.results["test_asset_resolution"] = resolved

            ctx.results["interpreted_intake"] = interpreted_intake
            self.memory.write(self._run_scope(ctx.run_id) + ":interpreted_intake", interpreted_intake, tier=MemoryTier.WORKING)
            bus = bus_for_run(ctx.run_id, self.memory)
            ob_count = await bus.index_obligations(
                ctx.project_id,
                interpreted_intake.get("obligations") or [],
            )
            if ob_count:
                append_run_event(
                    ctx.run_id,
                    "obligations_indexed",
                    f"Indexed {ob_count} obligations for pgvector retrieval",
                    stage=ctx.stage.value,
                )
            self.memory.write(
                self._run_scope(ctx.run_id) + ":documents",
                ctx.documents,
                tier=MemoryTier.WORKING,
            )
            append_run_event(
                ctx.run_id,
                "intake_normalized",
                f"Intake normalized via {norm_meta.get('method', 'unknown')}",
                stage=ctx.stage.value,
                details=norm_meta,
            )

            ctx.stage = PipelineStage.READINESS
            self._write_status(ctx)
            readiness = await self._execute_agent(
                "Readiness",
                self.readiness,
                {
                    "documents": ctx.documents,
                    "interpreted_intake": interpreted_intake,
                    "target_url": ctx.metadata.get("target_url"),
                    "metadata": ctx.metadata,
                },
            )
            ctx.results["readiness"] = readiness
            if readiness.get("status") == "NOT_READY" and readiness.get("block_run"):
                await self._emit(EventType.PROJECT_READY, ctx.run_id, {"status": "NOT_READY", "missing": readiness.get("missing_artifacts")})
                ctx.stage = PipelineStage.COMPLETED
                return await self._finalize(ctx, partial=True)
            if readiness.get("gaps"):
                append_run_event(
                    ctx.run_id,
                    "readiness_gaps",
                    f"Continuing with gaps: {', '.join(readiness.get('gaps', []))}",
                    stage=ctx.stage.value,
                    level="warn",
                )

            ctx.stage = PipelineStage.REQUIREMENTS
            self._write_status(ctx)
            reqs = await self._execute_agent(
                "Requirements",
                self.requirements,
                {"documents": ctx.documents, "interpreted_intake": interpreted_intake},
            )
            ctx.results["requirements"] = reqs
            try:
                bus = bus_for_run(ctx.run_id, self.memory)
                await bus.index_requirements(ctx.project_id, reqs.get("requirements", []))
                bus.publish("requirements", reqs.get("requirements", []))
            except Exception as exc:
                logger.warning("Knowledge index (requirements) skipped: %s", exc)

            # Phase 1 Optimization: Execute Coverage and SiteExploration in parallel
            # They are independent - Coverage analyzes requirements, SiteExploration analyzes target URL
            ctx.stage = PipelineStage.COVERAGE
            coverage_ctx = {"requirements": reqs.get("requirements", [])}
            site_ctx = {
                "run_id": ctx.run_id,
                "project_id": ctx.project_id,
                "interpreted_intake": interpreted_intake,
                "target_url": (interpreted_intake.get("environment") or {}).get("target_url"),
                "metadata": ctx.metadata,
            }

            parallel_results = await self._execute_agents_parallel(
                [
                    ("Coverage", self.coverage, coverage_ctx),
                    ("SiteExploration", self.site_exploration, site_ctx),
                ],
                ctx
            )
            self._check_parallel_results(parallel_results, ["Coverage", "SiteExploration"], ctx)
            cov, site = parallel_results
            ctx.results["coverage"] = cov
            ctx.results["site_exploration"] = site

            ctx.stage = PipelineStage.TEST_DESIGN
            td = await self._execute_agent(
                "TestDesign",
                self.test_design,
                {
                    "requirements": reqs.get("requirements", []),
                    "coverage": cov,
                    "acceptance_criteria": self._extract_acceptance_criteria(ctx.documents),
                    "interpreted_intake": interpreted_intake,
                    "site_exploration": site,
                },
            )
            ctx.results["test_design"] = td

            # Phase 1 Optimization: Execute Traceability and RiskAssessment in parallel
            # Traceability builds matrix, RiskAssessment scores - can run concurrently
            ctx.stage = PipelineStage.TRACEABILITY
            traceability_ctx = {"requirements": reqs.get("requirements", []), "functional_scenarios": td.get("functional_scenarios", [])}
            # Risk needs both coverage and traceability, but can start with coverage
            # We'll use coverage first, then update with traceability results if needed
            risk_ctx = {"coverage": cov, "traceability": [], "functional_scenarios": td.get("functional_scenarios", [])}

            parallel_results = await self._execute_agents_parallel(
                [
                    ("Traceability", self.traceability, traceability_ctx),
                    ("Risk", self.risk, risk_ctx),
                ],
                ctx
            )
            self._check_parallel_results(parallel_results, ["Traceability", "Risk"], ctx)
            tr, risk = parallel_results
            ctx.results["traceability"] = tr
            ctx.results["risk"] = risk

            ctx.stage = PipelineStage.KNOWLEDGE_RETRIEVAL
            kr = await self._execute_agent(
                "Knowledge",
                self.knowledge,
                {
                    "run_id": ctx.run_id,
                    "project_id": ctx.project_id,
                    "requirements": reqs.get("requirements", []),
                },
            )
            ctx.results["knowledge"] = kr

            if skip_artifact_persist:
                # run_full continues with automation — do not mark completed or persist phase-1-only artifacts.
                append_run_event(
                    ctx.run_id,
                    "phase1_complete",
                    "Planning phase complete; starting automation",
                    stage=PipelineStage.PLAYWRIGHT_ARCHITECT.value,
                )
                self.memory.write(
                    f"pipeline_status:{ctx.run_id}",
                    {
                        "run_id": ctx.run_id,
                        "stage": PipelineStage.PLAYWRIGHT_ARCHITECT.value,
                        "stage_label": stage_label(PipelineStage.PLAYWRIGHT_ARCHITECT.value),
                        "project_id": ctx.project_id,
                        "status": "running",
                        "results_keys": list(ctx.results.keys()),
                        "errors": ctx.errors,
                    },
                    tier=MemoryTier.SHORT_TERM,
                )
                return {
                    "run_id": ctx.run_id,
                    "project_id": ctx.project_id,
                    "status": "running",
                    "stage": PipelineStage.PLAYWRIGHT_ARCHITECT.value,
                    "start_time": ctx.start_time.isoformat(),
                    "results": ctx.results,
                    "errors": ctx.errors,
                    "partial": False,
                }

            ctx.stage = PipelineStage.EXECUTIVE_REPORTING
            report = await self._execute_agent("Executive", self.executive, {
                "readiness": readiness, "coverage": cov, "traceability": tr.get("traceability_matrix", []), "risk": risk,
            })
            ctx.results["executive_report"] = report
            from src.runtime_policy import data_provenance

            ctx.results["data_provenance"] = data_provenance()
            ctx.results["reports"] = build_all_reports(ctx.results)

            await self._emit(EventType.REPORT_GENERATED, ctx.run_id, {"reports": list(ctx.results.get("reports", {}).keys())})
            await self._emit(EventType.PROCESSING_COMPLETED, ctx.run_id, {"phase": 1})
            ctx.stage = PipelineStage.COMPLETED
            self._write_status(ctx, status_override="completed")
            append_run_event(
                ctx.run_id,
                "stage",
                "Finalizing reports and artifacts",
                stage=PipelineStage.COMPLETED.value,
            )
            result = await self._finalize(ctx)
            return deliver_run_artifacts(ctx.run_id, result)

        except RunCancelledError as e:
            ctx.errors.append({"error": str(e), "cancelled": True})
            append_run_event(
                ctx.run_id,
                "run_cancelled",
                "Pipeline cancelled by operator",
                stage="cancelled",
                level="warn",
            )
            ctx.stage = PipelineStage.CANCELLED
            return await self._finalize(ctx, cancelled=True, error=str(e))
        except Exception as e:
            ctx.errors.append({"error": str(e), "traceback": traceback.format_exc()})
            append_run_event(ctx.run_id, "run_failed", str(e), stage="failed", level="error")
            return await self._finalize(ctx, failed=True, error=str(e))
        finally:
            self._reset_pipeline_ctx(ctx_token)
            self._reset_run_scope(scope_token)

    async def run_full(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Fase 1 + Fases 2-5 with Nivel 3 heal loop."""
        phase1 = await self.run_phase1(payload, skip_artifact_persist=True)
        if phase1.get("status") == "awaiting_input":
            return phase1
        if phase1.get("status") == "cancelled":
            return phase1
        if phase1.get("status") not in {"completed", "running"} or phase1.get("partial"):
            return phase1

        run_id = phase1["run_id"]
        project_id = phase1.get("project_id", payload.get("project_id", "demo"))
        scope_token = self._bind_run_scope(run_id)
        ctx = PipelineContext(
            run_id=run_id,
            project_id=project_id,
            mode="full",
            results=phase1.get("results", phase1),
        )
        if phase1.get("start_time"):
            ctx.start_time = datetime.fromisoformat(phase1["start_time"])
        ctx.metadata = payload.get("metadata", {})
        ctx.documents = self.memory.read(self._run_scope(run_id) + ":documents") or []
        ctx_token = self._bind_pipeline_ctx(ctx)

        try:
            agent_ctx = self._build_agent_ctx(ctx, payload)
            return await self._run_automation_tail(ctx, agent_ctx)
        except RunCancelledError as e:
            append_run_event(ctx.run_id, "run_cancelled", str(e), stage="cancelled", level="warn")
            ctx.stage = PipelineStage.CANCELLED
            return await self._finalize(ctx, cancelled=True, error=str(e))
        except Exception as e:
            append_run_event(ctx.run_id, "run_failed", str(e), stage="failed", level="error")
            return await self._finalize(ctx, failed=True, error=str(e))
        finally:
            self._reset_pipeline_ctx(ctx_token)
            self._reset_run_scope(scope_token)

    async def resume_run(self, run_id: str, resume_from: str | None = None) -> Dict[str, Any]:
        """Resume a failed pipeline from the last checkpoint without re-running completed stages."""
        from src.orchestrator.stages import normalize_resume_stage, resume_stage_index
        from src.services.pipeline_checkpoint import load_resume_checkpoint

        checkpoint = load_resume_checkpoint(run_id)
        if not checkpoint:
            raise ValueError(f"Run {run_id} cannot be resumed (no failure checkpoint found)")

        start_stage = normalize_resume_stage(resume_from or checkpoint.get("failed_stage") or "")
        if not start_stage:
            raise ValueError(f"Run {run_id} has no resume stage")

        payload = {
            "run_id": run_id,
            "project_id": checkpoint.get("project_id", "demo"),
            "mode": checkpoint.get("mode", "full"),
            "metadata": dict(checkpoint.get("metadata") or {}),
            "partial_results": dict(checkpoint.get("partial_results") or {}),
            "resume_from": start_stage,
        }
        if checkpoint.get("start_time"):
            payload["start_time"] = checkpoint["start_time"]

        append_run_event(
            run_id,
            "run_resumed",
            f"Resuming pipeline from {start_stage}",
            stage=start_stage,
        )

        automation_start = PipelineStage.PLAYWRIGHT_ARCHITECT.value
        if resume_stage_index(start_stage) >= resume_stage_index(automation_start):
            return await self._resume_automation(payload, start_stage)
        return await self.run_phase1(payload)

    async def _resume_automation(self, payload: Dict[str, Any], start_stage: str) -> Dict[str, Any]:
        run_id = payload["run_id"]
        project_id = payload.get("project_id", "demo")
        scope_token = self._bind_run_scope(run_id)
        ctx = PipelineContext(
            run_id=run_id,
            project_id=project_id,
            mode=payload.get("mode", "full"),
            results=dict(payload.get("partial_results") or {}),
            metadata=payload.get("metadata", {}),
        )
        if payload.get("start_time"):
            ctx.start_time = datetime.fromisoformat(payload["start_time"])
        ctx.documents = self.memory.read(self._run_scope(run_id) + ":documents") or []
        ctx_token = self._bind_pipeline_ctx(ctx)

        try:
            agent_ctx = self._build_agent_ctx(ctx, payload)
            return await self._run_automation_tail(ctx, agent_ctx, resume_from=start_stage)
        except RunCancelledError as e:
            append_run_event(ctx.run_id, "run_cancelled", str(e), stage="cancelled", level="warn")
            ctx.stage = PipelineStage.CANCELLED
            return await self._finalize(ctx, cancelled=True, error=str(e))
        except Exception as e:
            append_run_event(ctx.run_id, "run_failed", str(e), stage="failed", level="error")
            return await self._finalize(ctx, failed=True, error=str(e))
        finally:
            self._reset_pipeline_ctx(ctx_token)
            self._reset_run_scope(scope_token)

    def _build_agent_ctx(self, ctx: PipelineContext, payload: Dict[str, Any]) -> Dict[str, Any]:
        reqs_block = ctx.results.get("requirements") or {}
        td_block = ctx.results.get("test_design") or {}
        return {
            "run_id": ctx.run_id,
            "execution_id": ctx.run_id,
            "project_id": ctx.project_id,
            "functional_scenarios": td_block.get("functional_scenarios", []),
            "requirements": reqs_block.get("requirements", []),
            "coverage": ctx.results.get("coverage", {}),
            "traceability_matrix": (ctx.results.get("traceability") or {}).get("traceability_matrix", []),
            "acceptance_criteria": self._extract_acceptance_criteria(ctx.documents),
            "metadata": ctx.metadata or payload.get("metadata", {}),
            "target_url": (ctx.metadata or {}).get("target_url") or payload.get("metadata", {}).get("target_url"),
            "interpreted_intake": ctx.results.get("interpreted_intake") or {},
            "documents": ctx.documents if hasattr(ctx, "documents") else [],
            "use_llm_enrichment": True,
        }

    def _golden_only_demo_enabled(self, agent_ctx: Dict[str, Any]) -> bool:
        from src.automation.bank_portal import is_bank_portal_context
        from src.automation.golden_playwright import use_golden_only, use_golden_playwright

        if not use_golden_only() or not use_golden_playwright():
            return False
        interpreted = agent_ctx.get("interpreted_intake") or {}
        obligations = interpreted.get("obligations") if isinstance(interpreted, dict) else []
        return is_bank_portal_context(
            {
                "target_url": agent_ctx.get("target_url") or "",
                "interpreted_intake": interpreted,
                "metadata": agent_ctx.get("metadata") or {},
            },
            obligations=obligations or [],
            target_url=agent_ctx.get("target_url") or "",
        )

    async def _materialize_golden_demo_bundle(
        self, ctx: PipelineContext, agent_ctx: Dict[str, Any]
    ) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        from src.automation.golden_playwright import GOLDEN_ID, materialize_golden_playwright

        run_id = ctx.run_id
        target_url = agent_ctx.get("target_url") or ""
        append_run_event(
            run_id,
            "golden_only",
            "Demo mode: materializing pre-built golden Playwright template (no AI codegen)",
            stage=PipelineStage.PLAYWRIGHT_GENERATOR.value,
        )
        ctx.stage = PipelineStage.PLAYWRIGHT_GENERATOR
        self._write_status(ctx)
        project_path = (Path("generated-test-projects") / ctx.project_id).resolve()
        written, plan = await asyncio.to_thread(
            materialize_golden_playwright,
            project_path,
            ctx.project_id,
            target_url,
            run_id,
        )
        gen: Dict[str, Any] = {
            "project_path": str(project_path),
            "files": written,
            "codegen": {"golden_template": GOLDEN_ID, "template_only": True},
            "golden_template": GOLDEN_ID,
            "template_only": True,
            "generation_execution": {"status": "completed", "mode": "golden_only"},
            **{k: plan[k] for k in ("scenarios", "suite", "aut_profile") if k in plan},
        }
        validation: Dict[str, Any] = {
            "status": "completed",
            "validation": {"ok": True, "skipped": True, "reason": "golden_only"},
        }
        await self._emit(
            EventType.TEST_PLAN_CREATED,
            run_id,
            {"suite": plan.get("suite"), "golden_only": True, "scenario_count": len(plan.get("scenarios") or [])},
        )
        await self._emit(
            EventType.PLAYWRIGHT_GENERATED,
            run_id,
            {"files": written, "golden_only": True, "golden_template": GOLDEN_ID},
        )
        return plan, gen, validation

    async def _run_automation_tail(
        self,
        ctx: PipelineContext,
        agent_ctx: Dict[str, Any],
        *,
        resume_from: str | None = None,
    ) -> Dict[str, Any]:
        run_id = ctx.run_id
        bus = bus_for_run(run_id, self.memory)
        reqs_block = ctx.results.get("requirements") or {}
        td_block = ctx.results.get("test_design") or {}
        bus.publish("requirements", reqs_block.get("requirements", []))
        bus.publish("functional_scenarios", td_block.get("functional_scenarios", []))
        bus.publish("coverage", ctx.results.get("coverage", {}))
        bus.publish("traceability_matrix", (ctx.results.get("traceability") or {}).get("traceability_matrix", []))
        bus.publish("risk", ctx.results.get("risk", {}))

        golden_only = self._golden_only_demo_enabled(agent_ctx)

        plan = ctx.results.get("test_plan")
        if golden_only and should_run_resume_stage(resume_from, PipelineStage.PLAYWRIGHT_ARCHITECT.value):
            ensure_not_cancelled(run_id)
            plan, gen, validation = await self._materialize_golden_demo_bundle(ctx, agent_ctx)
            ctx.results["test_plan"] = plan
            ctx.results["generated_code"] = gen
            ctx.results["playwright_validation"] = validation
        elif should_run_resume_stage(resume_from, PipelineStage.PLAYWRIGHT_ARCHITECT.value):
            ensure_not_cancelled(run_id)
            ctx.stage = PipelineStage.PLAYWRIGHT_ARCHITECT
            self._write_status(ctx)
            plan = await self._execute_agent("PWArchitect", self.pw_architect, agent_ctx)
            ctx.results["test_plan"] = plan
            await self._emit(EventType.TEST_PLAN_CREATED, run_id, {"suite": plan.get("suite")})
        elif not plan:
            raise ValueError("Cannot resume: missing test_plan in checkpoint")

        gen = ctx.results.get("generated_code")
        if golden_only and gen and gen.get("golden_template"):
            pass
        elif should_run_resume_stage(resume_from, PipelineStage.PLAYWRIGHT_GENERATOR.value):
            ensure_not_cancelled(run_id)
            ctx.stage = PipelineStage.PLAYWRIGHT_GENERATOR
            gen = await self._execute_agent("PWGenerator", self.pw_generator, {**agent_ctx, "test_plan": plan})
            ctx.results["generated_code"] = gen
            await self._emit(EventType.PLAYWRIGHT_GENERATED, run_id, {"files": gen.get("files", [])})
        elif not gen:
            raise ValueError("Cannot resume: missing generated_code in checkpoint")

        validation = ctx.results.get("playwright_validation")
        if golden_only and validation and validation.get("validation", {}).get("skipped"):
            pass
        elif should_run_resume_stage(resume_from, PipelineStage.PLAYWRIGHT_VALIDATION.value):
            ensure_not_cancelled(run_id)
            ctx.stage = PipelineStage.PLAYWRIGHT_VALIDATION
            self._write_status(ctx)
            validation = await self._execute_agent(
                "PWValidation",
                self.pw_validation,
                {**agent_ctx, "test_plan": plan, "generated_code": gen},
            )
            ctx.results["playwright_validation"] = validation
            if validation.get("status") == "validation_failed":
                gen = await self._maybe_template_fallback_after_validation_failure(ctx, gen, plan, agent_ctx)
                ctx.results["generated_code"] = gen
                validation = await self._execute_agent(
                    "PWValidation",
                    self.pw_validation,
                    {**agent_ctx, "test_plan": plan, "generated_code": gen},
                )
                ctx.results["playwright_validation"] = validation
        elif not validation:
            validation = {"status": "completed", "validation": {"ok": True}}
            ctx.results["playwright_validation"] = validation

        gen_exec = (gen.get("generation_execution") or {})
        skip_execution = (
            gen_exec.get("status") == "validation_failed"
            or validation.get("status") == "validation_failed"
        )

        exec_result = ctx.results.get("execution")
        heal_attempts = int(ctx.results.get("heal_attempts") or 0)
        if skip_execution:
            exec_result = self._compile_failure_execution_result(gen)
            ctx.results["execution"] = exec_result
            ctx.results["heal_attempts"] = 0
            append_run_event(
                run_id,
                "execution_skipped",
                "Playwright specs failed compile validation; execution not started",
                stage=PipelineStage.EXECUTION.value,
                level="error",
            )
        elif should_run_resume_stage(resume_from, PipelineStage.EXECUTION.value):
            demo_execution = golden_only or self._is_golden_template_run(gen)
            if demo_execution:
                exec_result, heal_attempts = await self._execute_demo_single_pass(ctx, gen, agent_ctx)
            else:
                exec_result, heal_attempts = await self._automation_loop(ctx, gen, agent_ctx, plan)
            ctx.results["execution"] = exec_result
            ctx.results["heal_attempts"] = heal_attempts
        elif not exec_result:
            raise ValueError("Cannot resume: missing execution results in checkpoint")

        if gen.get("project_path"):
            await asyncio.to_thread(snapshot_generated_scripts, run_id, Path(gen["project_path"]), gen)

        await self._collect_pipeline_evidence(ctx, gen, exec_result, heal_attempts)

        defects = ctx.results.get("defects")
        if should_run_resume_stage(resume_from, PipelineStage.DEFECT_DETECTION.value):
            ctx.stage = PipelineStage.DEFECT_DETECTION
            self._write_status(ctx)
            defects = await self._execute_agent(
                "DefectDetection",
                self.defect_detection,
                {
                    **agent_ctx,
                    "coverage": ctx.results.get("coverage"),
                    "readiness": ctx.results.get("readiness"),
                    "test_plan": ctx.results.get("test_plan"),
                    "generated_code": ctx.results.get("generated_code"),
                    "rca": ctx.results.get("rca"),
                    "healing": ctx.results.get("healing"),
                    "execution_results": exec_result,
                },
            )
            ctx.results["defects"] = defects

        deliverable = ctx.results.get("deliverable")
        if should_run_resume_stage(resume_from, PipelineStage.EXCEL_DELIVERABLE.value):
            ctx.stage = PipelineStage.EXCEL_DELIVERABLE
            self._write_status(ctx)
            deliverable = await self._execute_agent(
                "ExcelDeliverable",
                self.excel_deliverable,
                {
                    **agent_ctx,
                    "test_plan": ctx.results.get("test_plan"),
                    "execution_results": exec_result,
                    "execution": exec_result,
                },
            )
            ctx.results["deliverable"] = deliverable

        if should_run_resume_stage(resume_from, PipelineStage.EXECUTIVE_REPORTING.value):
            ctx.stage = PipelineStage.EXECUTIVE_REPORTING
            self._write_status(ctx)
            final_report = await self._execute_agent(
                "Executive",
                self.executive,
                {
                    **ctx.results,
                    "execution_results": exec_result,
                    "rca": ctx.results.get("rca"),
                    "healing": ctx.results.get("healing"),
                    "defects": defects,
                },
            )
            ctx.results["executive_report"] = final_report
            from src.runtime_policy import data_provenance

            ctx.results["data_provenance"] = data_provenance()
            ctx.results["reports"] = build_all_reports(ctx.results)
            try:
                await bus.index_pipeline_results(ctx.project_id, ctx.results)
            except Exception as exc:
                logger.warning("Knowledge index (pipeline) skipped: %s", exc)

        ctx.stage = PipelineStage.COMPLETED
        await self._emit(EventType.PROCESSING_COMPLETED, run_id, {"phase": "full"})
        result = await self._finalize_pipeline(ctx, exec_result)
        return deliver_run_artifacts(run_id, result)

    def _resolve_finalize_mode(
        self, exec_result: Dict[str, Any]
    ) -> tuple[bool, bool, str]:
        """Return (partial, failed, pipeline_status) for completion."""
        exec_failed = (
            int(exec_result.get("failed") or 0) > 0
            or exec_result.get("status") in ("failed", "error")
        )
        zero_tests = (
            int(exec_result.get("total") or 0) == 0
            and int(exec_result.get("passed") or 0) == 0
            and len(exec_result.get("executed_tests") or []) == 0
        )
        if zero_tests and exec_failed:
            return False, True, "failed"
        if exec_failed or zero_tests:
            return True, False, "partial"
        return False, False, "completed"

    async def _finalize_pipeline(
        self, ctx: PipelineContext, exec_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        partial, failed, pipeline_status = self._resolve_finalize_mode(exec_result)
        ctx.stage = PipelineStage.COMPLETED
        self._write_status(ctx, status_override=pipeline_status)
        append_run_event(
            ctx.run_id,
            "stage",
            "Finalizing reports and artifacts",
            stage=PipelineStage.COMPLETED.value,
        )
        if partial:
            return await self._finalize(ctx, partial=True)
        if failed:
            return await self._finalize(ctx, failed=True)
        return await self._finalize(ctx)

    def _is_bank_portal_plan(self, plan: Dict[str, Any]) -> bool:
        return (
            plan.get("aut_profile") == "bank_portal"
            or (plan.get("suite") or {}).get("aut_profile") == "bank_portal"
        )

    async def _maybe_template_fallback_after_validation_failure(
        self,
        ctx: PipelineContext,
        gen: Dict[str, Any],
        plan: Dict[str, Any],
        agent_ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        gen_exec = gen.get("generation_execution") or {}
        if gen_exec.get("status") != "validation_failed":
            return gen
        if not self._is_bank_portal_plan(plan):
            return gen
        project_path = gen.get("project_path")
        if not project_path:
            return gen
        fallback_ctx = {
            **agent_ctx,
            "test_plan": plan,
            "exploration": (gen.get("exploration") or {}),
        }
        regen = await self.pw_generator.regenerate_template_specs(
            fallback_ctx, plan, Path(project_path)
        )
        merged = {**gen, **regen, "template_only": True}
        merged["generation_execution"] = regen.get("generation_execution") or gen_exec
        if regen.get("ok"):
            append_run_event(
                ctx.run_id,
                "template_fallback_applied",
                "Regenerated bank portal specs from obligation templates after validation failure",
                stage=PipelineStage.PLAYWRIGHT_GENERATOR.value,
            )
        return merged

    @staticmethod
    def _compile_failure_execution_result(gen: Dict[str, Any]) -> Dict[str, Any]:
        gen_exec = gen.get("generation_execution") or {}
        stderr = ""
        for step in gen_exec.get("steps") or []:
            stderr = step.get("stderr") or step.get("stderr_tail") or stderr
        message = (stderr or "Playwright specs failed compile validation").strip()[:500]
        failures = [
            {
                "title": "compile",
                "error": {"message": message, "category": "compile"},
            }
        ]
        return {
            "status": "failed",
            "mode": "skipped",
            "passed": 0,
            "failed": 1,
            "total": 0,
            "pass_rate": 0.0,
            "duration_seconds": 0.0,
            "artifacts": {"screenshots": [], "traces": [], "videos": []},
            "failures": failures,
            "executed_tests": [],
            "compile_errors": 1,
            "stderr": stderr[:2000] if stderr else message,
        }

    @staticmethod
    def _is_golden_template_run(gen: Dict[str, Any]) -> bool:
        codegen = gen.get("codegen") or {}
        return bool(
            gen.get("golden_template")
            or gen.get("template_only")
            or codegen.get("golden_template")
            or codegen.get("template_only")
        )

    async def _execute_demo_single_pass(
        self, ctx: PipelineContext, gen: Dict[str, Any], agent_ctx: Dict[str, Any]
    ) -> tuple[Dict[str, Any], int]:
        """Run golden Playwright suite once — no RCA, healing, or re-execution."""
        ctx.stage = PipelineStage.EXECUTION
        self._write_status(ctx)
        append_run_event(
            ctx.run_id,
            "golden_demo_execution",
            "Demo mode: single Playwright pass using golden template (no healing/re-execution)",
            stage=PipelineStage.EXECUTION.value,
        )
        await self._emit(
            EventType.EXECUTION_STARTED,
            ctx.run_id,
            {"attempt": 0, "demo_mode": "golden_template", "healing_disabled": True},
        )
        project_path = gen.get("project_path")
        exec_result = await self._execute_agent(
            "Execution", self.execution, {**agent_ctx, "project_path": project_path}
        )
        await self._emit(
            EventType.EXECUTION_COMPLETED,
            ctx.run_id,
            {"pass_rate": exec_result.get("pass_rate"), "demo_mode": "golden_template"},
        )
        if exec_result.get("failed", 0):
            await self._emit(
                EventType.TEST_FAILED,
                ctx.run_id,
                {"failures": exec_result.get("failures", []), "healing_skipped": True},
            )
        return exec_result, 0

    async def _automation_loop(
        self, ctx: PipelineContext, gen: Dict, agent_ctx: Dict, plan: Dict[str, Any]
    ) -> tuple:
        exec_result = {}
        heal_attempts = 0
        project_path = gen.get("project_path")
        is_bank_portal = self._is_bank_portal_plan(plan)

        # OPTIMIZATION: Bank portal uses deterministic templates - limit healing to save costs
        # Template-based specs are more reliable; excessive healing attempts waste LLM tokens
        effective_max_heal = 1 if is_bank_portal else self.max_heal_attempts

        for attempt in range(effective_max_heal + 1):
            ctx.stage = PipelineStage.EXECUTION if attempt == 0 else PipelineStage.REEXECUTION
            self._write_status(ctx)
            await self._emit(EventType.EXECUTION_STARTED, ctx.run_id, {"attempt": attempt})
            exec_result = await self._execute_agent("Execution", self.execution, {**agent_ctx, "project_path": project_path})
            await self._emit(EventType.EXECUTION_COMPLETED, ctx.run_id, {"pass_rate": exec_result.get("pass_rate")})

            if exec_result.get("failed", 0) == 0:
                break

            await self._emit(EventType.TEST_FAILED, ctx.run_id, {"failures": exec_result.get("failures", [])})
            ctx.stage = PipelineStage.FAILURE_RCA
            rca = await self._execute_agent("RCA", self.rca, {**agent_ctx, "execution_results": exec_result})
            ctx.results["rca"] = rca
            await self._emit(EventType.RCA_COMPLETED, ctx.run_id, {"confidence": rca.get("confidence")})

            if attempt >= effective_max_heal:
                break

            # OPTIMIZATION: Early exit if RCA confidence is too low - healing likely to fail
            # Saves unnecessary LLM calls when root cause is unclear
            rca_confidence = rca.get("confidence", 0.5)
            if rca_confidence < 0.4:
                logger.info("RCA confidence %.2f too low for healing; skipping heal loop", rca_confidence)
                append_run_event(
                    ctx.run_id,
                    "healing_skipped_low_confidence",
                    f"RCA confidence {rca_confidence:.2f} below threshold (0.4); skipping healing",
                    stage=PipelineStage.SELF_HEALING.value,
                    level="info",
                )
                break

            # OPTIMIZATION: Skip LLM-based healing for bank portal compile failures
            # Use deterministic template regeneration instead
            if self._is_compile_failure(exec_result, rca) and is_bank_portal:
                if project_path:
                    regen = await self.pw_generator.regenerate_template_specs(
                        {**agent_ctx, "test_plan": plan, "exploration": gen.get("exploration") or {}},
                        plan,
                        Path(project_path),
                    )
                    if regen.get("ok"):
                        heal_attempts += 1
                        ctx.results["healing"] = {
                            "healed": True,
                            "reason": "template_spec_regeneration",
                            "changes": [],
                        }
                        await self._emit(
                            EventType.HEALING_COMPLETED,
                            ctx.run_id,
                            {"healed": True, "strategy": "template_spec_regeneration"},
                        )
                        continue
                # If regen fails, fall through to regular healing as last resort

            ctx.stage = PipelineStage.SELF_HEALING
            await self._emit(EventType.HEALING_STARTED, ctx.run_id, {})
            heal = await self._execute_agent("Healing", self.healing, {**agent_ctx, "rca": rca, "project_path": project_path})
            ctx.results["healing"] = heal
            heal_attempts += 1
            await self._emit(EventType.HEALING_COMPLETED, ctx.run_id, {"healed": heal.get("healed")})

            if not heal.get("healed"):
                break
            await self._emit(EventType.REEXECUTION_COMPLETED, ctx.run_id, {"attempt": attempt + 1})

        return exec_result, heal_attempts

    @staticmethod
    def _is_compile_failure(exec_result: Dict[str, Any], rca: Dict[str, Any]) -> bool:
        if exec_result.get("compile_errors"):
            return True
        category = (rca.get("category") or rca.get("failure_category") or "").lower()
        if category in {"compile", "import", "syntax"}:
            return True
        for failure in exec_result.get("failures") or []:
            err = failure.get("error") or {}
            if err.get("category") == "compile":
                return True
            msg = (err.get("message") or "").lower()
            if "syntaxerror" in msg or "has already been declared" in msg:
                return True
        for candidate in rca.get("healing_candidates") or []:
            cat = (candidate.get("category") or "").lower()
            if cat in {"compile", "import", "syntax"}:
                return True
        return False

    async def _collect_pipeline_evidence(
        self, ctx: PipelineContext, gen: Dict, exec_result: Dict[str, Any], heal_attempts: int
    ) -> None:
        project_path = gen.get("project_path")
        if not project_path or not exec_result:
            return
        target_url = (ctx.metadata or {}).get("target_url") or ""
        interpreted = ctx.results.get("interpreted_intake") or {}
        obligations = interpreted.get("obligations") or []
        await asyncio.to_thread(
            collect_and_persist_evidence,
            ctx.run_id,
            Path(project_path),
            exec_result,
            attempt=heal_attempts,
            project_id=ctx.project_id,
            target_url=target_url,
            obligations=obligations,
        )
        from src.services.project_env import strip_secret_env_vars

        await asyncio.to_thread(strip_secret_env_vars, Path(project_path))
        append_run_event(
            ctx.run_id,
            "evidence_collected",
            f"Collected {exec_result.get('evidence', {}).get('screenshot_count', 0)} screenshots",
            stage="execution",
        )

    async def _init_context(self, payload: Dict[str, Any]) -> tuple[PipelineContext, Any]:
        run_id = payload.get("run_id") or f"run-{uuid.uuid4().hex[:12]}"
        project_id = payload.get("project_id", "demo")
        scope_token = self._bind_run_scope(run_id)

        documents = payload.get("documents", [])
        force_reprocess = payload.get("force_reprocess", False)

        if not documents and payload.get("local_intake_root"):
            from pathlib import Path

            from src.connectors.google_drive.intake import fetch_documents_from_local_root

            documents = await fetch_documents_from_local_root(
                Path(payload["local_intake_root"]),
                project_id=project_id,
                force_reprocess=force_reprocess,
            )

        if not documents and payload.get("document_paths"):
            from pathlib import Path

            from src.connectors.google_drive.intake import fetch_documents_from_local_root

            first = Path(payload["document_paths"][0])
            if first.exists():
                root = first.parent
                for _ in range(4):
                    if (root / "manifest.json").exists() or (root / "intake").is_dir():
                        documents = await fetch_documents_from_local_root(
                            root,
                            project_id=project_id,
                            force_reprocess=force_reprocess,
                        )
                        break
                    if root.parent == root:
                        break
                    root = root.parent
            if not documents:
                for p in payload["document_paths"]:
                    doc = self.parser.parse_file(Path(p))
                    doc = self.metadata_extractor.extract(doc)
                    documents.append(doc.to_dict())

        if not documents and payload.get("use_system_root"):
            from src.connectors.google_drive.intake import fetch_documents_from_folder
            from src.connectors.google_drive.system_root import list_project_folders_under_system_root

            projects = await list_project_folders_under_system_root()
            folder_filter = payload.get("project_folder_name")
            match = None
            for pf in projects:
                if folder_filter and pf.folder_name != folder_filter:
                    continue
                if pf.project_id == project_id or folder_filter:
                    match = pf
                    break
            if match is None and projects:
                match = projects[0]
            if match:
                documents = await fetch_documents_from_folder(
                    match.drive_folder_id,
                    project_id=match.project_id,
                    force_reprocess=force_reprocess,
                )
                if match.metadata:
                    ctx_meta = payload.get("metadata") or {}
                    ctx_meta.update(match.metadata)
                    payload["metadata"] = ctx_meta

        if not documents and payload.get("drive_folder_id"):
            from src.connectors.google_drive.intake import fetch_documents_from_folder

            documents = await fetch_documents_from_folder(
                payload["drive_folder_id"],
                project_id=project_id,
                force_reprocess=force_reprocess,
            )

        if not documents:
            cached = self.memory.read(self._run_scope(run_id) + ":documents")
            if cached:
                documents = cached

        if not documents:
            from src.runtime_policy import RealDataRequiredError, allow_mocks

            if allow_mocks():
                documents = self._default_fixture_documents()
            else:
                raise RealDataRequiredError(
                    "No documents found for intake. Provide drive_folder_id, document_paths, "
                    "or a populated Google Drive project folder — fixture/sample documents are disabled."
                )

        ctx = PipelineContext(run_id=run_id, project_id=project_id, documents=documents, metadata=payload.get("metadata", {}))
        ctx.mode = payload.get("mode", "full")
        from src.services.run_hints import merge_hints_into_metadata

        ctx.metadata = merge_hints_into_metadata(ctx.metadata, run_id)
        ctx.stage = PipelineStage.INTAKE
        self.memory.write(self._run_scope(run_id) + ":documents", documents, tier=MemoryTier.WORKING)
        payload.setdefault("run_id", run_id)
        return ctx, scope_token

    @staticmethod
    def _extract_acceptance_criteria(documents: List[Dict]) -> List[Dict[str, Any]]:
        criteria = []
        for doc in documents:
            if (doc.get("document_type") or "").upper() == "AC":
                text = doc.get("raw_text") or ""
                if doc.get("sections"):
                    text = "\n".join(s.get("content", "") for s in doc["sections"][:8])
                criteria.append(
                    {
                        "id": doc.get("id") or doc.get("filename"),
                        "source": doc.get("filename"),
                        "summary": text[:1200],
                    }
                )
        return criteria

    def _default_fixture_documents(self) -> List[Dict]:
        from pathlib import Path
        fixtures = Path("tests/fixtures/documents")
        docs = []
        if fixtures.exists():
            for p in fixtures.glob("*.txt"):
                doc = self.parser.parse_file(p)
                doc = self.metadata_extractor.extract(doc)
                docs.append(doc.to_dict())
        if not docs:
            for dtype in ("BRD", "PRD", "UC", "AC"):
                docs.append({"id": dtype.lower(), "filename": f"{dtype.lower()}.txt", "document_type": dtype, "raw_text": f"Sample {dtype} content", "sections": []})
        return docs

    async def _finalize(
        self,
        ctx: PipelineContext,
        partial: bool = False,
        failed: bool = False,
        cancelled: bool = False,
        awaiting_input: bool = False,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        ctx.end_time = datetime.utcnow()
        duration = (ctx.end_time - ctx.start_time).total_seconds()
        status = (
            "cancelled"
            if cancelled
            else (
                "failed"
                if failed
                else ("awaiting_input" if awaiting_input else ("partial" if partial else "completed"))
            )
        )
        failed_at_stage = ctx.stage.value
        display_stage = (
            "cancelled" if cancelled else ("failed" if failed else ctx.stage.value)
        )
        from src.runtime_policy import data_provenance, ensure_not_mock_execution_result

        execution = ctx.results.get("execution")
        if execution:
            ensure_not_mock_execution_result(execution)

        result = {
            "run_id": ctx.run_id,
            "project_id": ctx.project_id,
            "mode": ctx.mode,
            "metadata": ctx.metadata,
            "status": status,
            "stage": display_stage,
            "failed_at_stage": failed_at_stage if failed else None,
            "start_time": ctx.start_time.isoformat(),
            "end_time": ctx.end_time.isoformat(),
            "duration_seconds": duration,
            "results": ctx.results,
            "errors": ctx.errors,
            "partial": partial,
            "data_provenance": data_provenance(),
        }
        if error:
            result["error"] = error
        if cancelled:
            result["cancelled_at_stage"] = failed_at_stage
        if awaiting_input:
            result["awaiting"] = "target_url"
        if failed and ctx.stage not in (PipelineStage.AWAITING_TARGET_URL, PipelineStage.FAILED):
            from src.services.pipeline_checkpoint import save_failure_checkpoint

            save_failure_checkpoint(
                ctx.run_id,
                failed_stage=failed_at_stage,
                project_id=ctx.project_id,
                partial_results=ctx.results,
                metadata={**(ctx.metadata or {}), "drive_folder_id": (ctx.metadata or {}).get("drive_folder_id")},
                start_time=ctx.start_time.isoformat(),
                mode=ctx.mode,
            )
            result["resumable"] = True
            result["resume_from"] = failed_at_stage
        elif status == "completed":
            from src.services.pipeline_checkpoint import clear_checkpoint, load_checkpoint

            cp = load_checkpoint(ctx.run_id)
            if cp and cp.get("awaiting") == "resume":
                clear_checkpoint(ctx.run_id)
        self.memory.write(f"pipeline_summary:{ctx.run_id}", result, tier=MemoryTier.LONG_TERM)
        self.memory.write(f"pipeline_status:{ctx.run_id}", result, tier=MemoryTier.SHORT_TERM)
        try:
            artifact_dir = await asyncio.to_thread(write_run_reports_to_disk, ctx.run_id, result)
            if artifact_dir:
                result["artifact_dir"] = str(artifact_dir)
                self.memory.write(f"pipeline_summary:{ctx.run_id}", result, tier=MemoryTier.LONG_TERM)
        except Exception as exc:
            logger.warning("Could not persist run artifacts for %s: %s", ctx.run_id, exc)
        clear_cancel(ctx.run_id)
        return result

    def get_pipeline_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        summary = self.memory.read(f"pipeline_summary:{run_id}")
        if summary:
            return summary
        return self.memory.read(f"pipeline_status:{run_id}")

    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from src.runtime_policy import validate_startup_configuration

        await validate_startup_configuration()
        mode = payload.get("mode", "full")
        if mode == "phase1":
            return await self.run_phase1(payload)
        return await self.run_full(payload)
