"""Traceability matrix agent."""
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.agents.memory_bus import _requirements_list


class TraceabilityAgent(BaseAgent):
    """Build bidirectional requirement/test traceability matrix.

    Memory contract:
      Reads: requirements, functional_scenarios, obligations, interpreted_intake
      Writes: traceability_matrix
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        requirements = _requirements_list(
            context.get("requirements") or self.read_memory("requirements", default=[])
        )
        scenarios = context.get("functional_scenarios") or self.read_memory("functional_scenarios", default=[])
        obligations = context.get("obligations") or self.read_memory("obligations", default=[])
        interpreted = context.get("interpreted_intake") or self.read_memory("interpreted_intake", default={})
        if not obligations:
            obligations = interpreted.get("obligations") or []

        fallback = {"matrix": self._build_matrix(requirements, scenarios, obligations), "validation_issues": []}
        task = f"""TAREA: Construir matriz de trazabilidad bidireccional REQ/TEST y validar cobertura 1:1.
Reglas:
- Cada obligación automatable debe mapear a al menos un escenario.
- Cada obligación manual debe quedar documentada (test_id null, coverage manual).
- Devuelve JSON: {{"matrix": [{{"requirement_id", "test_id", "coverage": "full|partial|none|manual", "rationale"}}], "validation_issues": [string]}}.

OBLIGACIONES:
{self.compact_json(obligations)}

REQUISITOS:
{self.compact_json(requirements)}

ESCENARIOS:
{self.compact_json(scenarios)}
"""
        result = await self.ask_json(task, fallback, temperature=0.1, use_fallback_on_failure=True)

        matrix = result.get("matrix") or result.get("traceability_matrix") or []
        result.setdefault("traceability_matrix", matrix)
        result.setdefault("validation_issues", self._validate_1to1(obligations, scenarios, matrix))
        self.store_short_term_memory("traceability_matrix", result["traceability_matrix"])
        return result

    def _build_matrix(
        self,
        requirements: List[Dict],
        scenarios: List[Dict],
        obligations: List[Dict] | None = None,
    ) -> List[Dict]:
        req_ids = {r.get("id") for r in requirements}
        # Content-first intake has no discrete requirements — anchor the matrix on
        # obligations using synthetic REQ-{id} keys so covered obligations are not
        # falsely reported as "sin escenario mapeado".
        if not req_ids and obligations:
            return self._build_obligation_matrix(obligations, scenarios)

        matrix = []
        for sc in scenarios:
            for rid in sc.get("requirement_ids", []):
                if rid in req_ids or not req_ids:
                    matrix.append({"requirement_id": rid, "test_id": sc.get("id"), "coverage": "full"})
        for req in requirements:
            rid = req.get("id")
            if not any(m["requirement_id"] == rid for m in matrix):
                matrix.append({"requirement_id": rid, "test_id": None, "coverage": "none"})
        return matrix

    def _build_obligation_matrix(self, obligations: List[Dict], scenarios: List[Dict]) -> List[Dict]:
        scenario_by_ob: Dict[str, Dict] = {}
        for sc in scenarios:
            if not isinstance(sc, dict):
                continue
            mapped_ids = list(sc.get("requirement_ids") or []) + list(sc.get("obligation_ids") or [])
            for rid in mapped_ids:
                scenario_by_ob.setdefault(str(rid), sc)

        matrix = []
        for ob in obligations:
            if not isinstance(ob, dict):
                continue
            ob_id = str(ob.get("id") or "")
            if not ob_id:
                continue
            synthetic_id = f"REQ-{ob_id}"
            tier = ob.get("automation_tier", "partial")
            sc = scenario_by_ob.get(ob_id)
            if sc:
                coverage = "full"
                test_id = sc.get("id")
            elif tier == "manual":
                coverage = "manual"
                test_id = None
            else:
                coverage = "none"
                test_id = None
            matrix.append(
                {
                    "requirement_id": synthetic_id,
                    "obligation_id": ob_id,
                    "test_id": test_id,
                    "coverage": coverage,
                }
            )
        return matrix

    def _validate_1to1(self, obligations: List[Dict], scenarios: List[Dict], matrix: List[Dict]) -> List[str]:
        issues = []
        covered = set()
        for m in matrix:
            if m.get("coverage") not in ("full", "partial", "manual"):
                continue
            if m.get("requirement_id") is not None:
                covered.add(str(m.get("requirement_id")))
            if m.get("obligation_id") is not None:
                covered.add(str(m.get("obligation_id")))
        for ob in obligations:
            if not isinstance(ob, dict):
                continue
            ob_id = str(ob.get("id") or "")
            is_covered = ob_id in covered or f"REQ-{ob_id}" in covered
            tier = ob.get("automation_tier", "partial")
            if tier in ("automated", "partial"):
                if not is_covered:
                    issues.append(f"Obligación automatable {ob_id} sin escenario mapeado")
            elif tier == "manual":
                if not is_covered:
                    issues.append(f"Obligación manual {ob_id} no documentada en matriz")
        return issues
