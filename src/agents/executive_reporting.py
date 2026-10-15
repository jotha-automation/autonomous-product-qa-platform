"""Executive reporting agent."""
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.reporting.error_sanitization import validation_blocked_context
from src.reporting.numeric import coerce_score_float
from src.reporting.scores import compute_release_readiness_score


def _traceability_list(traceability: Any) -> List[Dict[str, Any]]:
    if isinstance(traceability, list):
        return traceability
    if isinstance(traceability, dict):
        return traceability.get("traceability_matrix") or traceability.get("matrix") or []
    return []


def compute_design_readiness_score(
    readiness: Dict[str, Any],
    traceability: List[Dict[str, Any]],
    test_plan: Dict[str, Any],
) -> float:
    """Score QA design/traceability readiness when execution cannot validate the product."""
    req_quality = coerce_score_float(readiness.get("score", 0.5))
    if traceability:
        traced_weight = sum(
            1.0
            if t.get("coverage") in ("full", "partial")
            else 0.5
            if t.get("coverage") == "manual"
            else 0.0
            for t in traceability
        )
        trace_score = traced_weight / max(len(traceability), 1)
    else:
        trace_score = 0.75
    scenarios = len(test_plan.get("scenarios") or [])
    design_factor = 0.9 if scenarios >= 10 else (0.75 if scenarios else 0.5)
    normalized = max(0.0, min(1.0, req_quality)) * max(0.0, min(1.0, trace_score)) * design_factor
    return round(normalized * 100, 2)


class ExecutiveReportingAgent(BaseAgent):
    """Compose executive summary for stakeholders.

    Memory contract:
      Reads: readiness_status, coverage_gaps, traceability_matrix, risk_scores,
             execution_results, rca, healing, requirements, defects
      Writes: executive_report
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        readiness = context.get("readiness") or self.read_memory("readiness_status", default={})
        if isinstance(readiness, str):
            readiness = {"status": readiness, "score": 1.0 if readiness == "READY" else 0.3}
        coverage = context.get("coverage") or self.read_memory("coverage_gaps", default={})
        traceability = context.get("traceability") or self.read_memory("traceability_matrix", default=[])
        trace_list = _traceability_list(traceability)
        risk = context.get("risk") or self.read_memory("risk_scores", default={})
        execution = context.get("execution_results") or self.read_memory("execution_results", default={})
        rca = context.get("rca") or self.read_memory("rca", default={})
        healing = context.get("healing") or self.read_memory("healing", default={})
        test_plan = context.get("test_plan") or self.read_memory("test_plan", default={})

        validation_ctx = validation_blocked_context(execution, rca)
        validation_blocked = validation_ctx.get("validation_blocked", False)

        release_score = compute_release_readiness_score(
            coverage, risk, trace_list, readiness, execution=execution
        )
        design_score = compute_design_readiness_score(readiness, trace_list, test_plan)
        display_score = design_score if validation_blocked else release_score

        requirements = context.get("requirements") or self.read_memory("requirements", default=[])
        defects = context.get("defects") or self.read_memory("defects", default={})

        scenario_count = len(test_plan.get("scenarios") or [])
        failed = int(execution.get("failed") or 0)
        total = int(execution.get("total") or 0)

        fallback = self._build_fallback(
            validation_blocked=validation_blocked,
            display_score=display_score,
            design_score=design_score,
            release_score=release_score,
            scenario_count=scenario_count,
            failed=failed,
            total=total,
            rca=rca,
            defects=defects,
        )

        validation_guidance = ""
        if validation_blocked:
            validation_guidance = (
                "\nIMPORTANT: Automated validation was BLOCKED by infrastructure/ops issues "
                "(Playwright browser executable, Docker image mismatch, etc.). "
                "Distinguish clearly:\n"
                "- environment_not_ready: automation stack could not run — NOT a product defect verdict\n"
                "- product_not_ready: only when functional failures were observed in a working environment\n"
                f"Use design_readiness_score={design_score}% for QA design progress; "
                f"do NOT claim 0% product readiness solely because tests could not launch."
            )

        rca_summary = {k: rca.get(k) for k in ("failure_category", "root_cause", "confidence")}
        task = f"""TAREA: Generar resumen ejecutivo de release QA para liderazgo de ingeniería/producto.
Incluye defectos detectados y requerimientos cubiertos.
Devuelve JSON: {{
  "executive_summary", "technical_summary", "product_summary", "recommendations",
  "defect_summary", "environment_status", "product_readiness_verdict"
}}.

environment_status: "ready"|"not_ready" — automation/runtime stack
product_readiness_verdict: "validated"|"not_validated"|"blocked_by_environment" — functional QA outcome
validation_blocked: {validation_blocked}
design_readiness_score: {design_score}
release_readiness_score (execution-informed): {release_score}
display_readiness_score: {display_score}
Escenarios diseñados: {scenario_count}
Cobertura: {self.compact_json(coverage)}
Riesgo: {self.compact_json(risk)}
Trazabilidad: {self.compact_json(trace_list)}
Ejecución: {self.compact_json(execution)}
RCA: {self.compact_json(rca_summary)}
Defectos: {self.compact_json(defects)}
Requerimientos: {self.compact_json(requirements)}
{validation_guidance}
"""
        result = await self.ask_json(task, fallback, temperature=0.1, use_fallback_on_failure=True)

        result["release_readiness_score"] = display_score
        result["design_readiness_score"] = design_score
        result["execution_informed_score"] = release_score
        result.update(validation_ctx)
        result.setdefault("environment_status", "not_ready" if validation_blocked else "ready")
        result.setdefault(
            "product_readiness_verdict",
            "blocked_by_environment" if validation_blocked else "not_validated",
        )
        result.setdefault("generation_mode", "llm")
        result["reports"] = {
            "qa": {"coverage": coverage, "traceability_count": len(trace_list)},
            "automation": execution,
            "rca": rca,
            "healing": healing,
            "defects": {
                "total": defects.get("total", 0),
                "blocking": defects.get("blocking", 0),
                "validation_blocked": validation_blocked,
            },
        }
        self.store_short_term_memory("executive_report", result)
        return result

    @staticmethod
    def _build_fallback(
        *,
        validation_blocked: bool,
        display_score: float,
        design_score: float,
        release_score: float,
        scenario_count: int,
        failed: int,
        total: int,
        rca: Dict[str, Any],
        defects: Dict[str, Any],
    ) -> Dict[str, Any]:
        if validation_blocked:
            root = str(rca.get("root_cause") or "Incompatibilidad en la infraestructura de automatización.")
            return {
                "executive_summary": (
                    f"La validación automatizada no pudo ejecutarse por un problema de entorno "
                    f"(readiness de diseño QA: {design_score:.1f}%). "
                    f"No se puede emitir veredicto de producto hasta corregir la infraestructura. "
                    f"{root[:280]}"
                ),
                "technical_summary": (
                    f"Ejecución bloqueada: {failed}/{total} casos no pudieron lanzar el navegador Playwright. "
                    f"Categoría RCA: {rca.get('failure_category', 'infra')}. "
                    "Actualice la imagen Docker/Playwright y re-ejecute el pipeline."
                ),
                "product_summary": (
                    f"Se diseñaron {scenario_count} escenarios de prueba y la documentación de intake está lista, "
                    "pero la funcionalidad del producto no fue ejercitada en esta corrida."
                ),
                "recommendations": [
                    "Corregir la infraestructura de automatización (imagen Docker / Playwright browsers).",
                    "Re-ejecutar el pipeline para obtener evidencia funcional válida.",
                    "No tratar los fallos de lanzamiento del navegador como defectos de aplicación.",
                ],
                "defect_summary": (
                    f"1 bloqueador de infraestructura consolidado "
                    f"({defects.get('suppressed_duplicate_failures', failed)} fallos duplicados suprimidos)."
                ),
                "environment_status": "not_ready",
                "product_readiness_verdict": "blocked_by_environment",
            }

        return {
            "executive_summary": f"Release readiness score: {display_score:.1f}%",
            "technical_summary": "See coverage and traceability reports.",
            "product_summary": "Functional scenarios designed.",
            "recommendations": [],
            "defect_summary": f"{defects.get('total', 0)} defects reported.",
            "environment_status": "ready",
            "product_readiness_verdict": "not_validated" if failed else "validated",
        }
