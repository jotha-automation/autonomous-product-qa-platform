"""Risk assessment agent."""
from typing import Any, Dict

from src.agents.base import BaseAgent


class RiskAssessmentAgent(BaseAgent):
    """Evaluate release risk from coverage and traceability gaps.

    Memory contract:
      Reads: coverage_gaps, traceability_matrix
      Writes: risk_scores
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        coverage = context.get("coverage") or self.read_memory("coverage_gaps", default={})
        traceability = context.get("traceability") or self.read_memory("traceability_matrix", default=[])
        if isinstance(traceability, dict):
            traceability = traceability.get("traceability_matrix", traceability.get("matrix", []))

        fallback = self._compute(coverage, traceability)
        task = f"""TAREA: Evaluar riesgo de release a partir de cobertura y trazabilidad.
Devuelve JSON: {{"risk_scores": {{}}, "release_risk": "low|medium|high", "release_risk_score": number (0-1)}}.

COBERTURA:
{self.compact_json(coverage)}

TRAZABILIDAD:
{self.compact_json(traceability)}
"""
        result = await self.ask_json(task, fallback, temperature=0.1, use_fallback_on_failure=True)

        self.store_short_term_memory("risk_scores", result)
        return result

    def _compute(self, coverage: Dict, traceability: list) -> Dict[str, Any]:
        gaps = len(coverage.get("gaps", []))
        untested = len([t for t in traceability if t.get("coverage") == "none"])
        score = min(1.0, (gaps * 0.1 + untested * 0.05))
        risk = "low" if score < 0.3 else "medium" if score < 0.6 else "high"
        return {"risk_scores": {"functional": score}, "release_risk": risk, "release_risk_score": score}
