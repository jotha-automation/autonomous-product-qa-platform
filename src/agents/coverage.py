"""Coverage analysis agent."""
from typing import Any, Dict, List, Set

from src.agents.base import BaseAgent
from src.agents.memory_bus import _requirements_list


class CoverageAgent(BaseAgent):
    """Analyze coverage of obligations vs designed scenarios.

    Memory contract:
      Reads: requirements, functional_scenarios, obligations, interpreted_intake
      Writes: coverage_gaps
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

        fallback = self._rule_based(requirements)
        task = f"""TAREA: Analizar cobertura de requisitos y obligaciones vs escenarios generados.
Compara cada obligación/requisito con los escenarios y reporta brechas concretas.
Devuelve JSON: {{
  "gaps": [{{"id": string, "description": string, "missing_scenarios": boolean, "suggested_scenario": string}}],
  "duplicates": [string],
  "conflicts": [string],
  "coverage_score": number (0-1),
  "obligation_coverage": [{{"obligation_id": string, "covered": boolean, "scenario_ids": [string], "gap_reason": string}}]
}}.

OBLIGACIONES:
{self.compact_json(obligations)}

REQUISITOS:
{self.compact_json(requirements)}

ESCENARIOS GENERADOS:
{self.compact_json(scenarios)}
"""
        result = await self.ask_json(task, fallback, temperature=0.1, use_fallback_on_failure=True)

        result.setdefault("gaps", [])
        result.setdefault("duplicates", [])
        result.setdefault("conflicts", [])
        result.setdefault("coverage_score", 0.8)

        negative_count = sum(
            1 for s in scenarios if isinstance(s, dict) and s.get("scenario_type") not in (None, "happy_path")
        )
        result.setdefault(
            "negative_coverage",
            {
                "negative_scenarios": negative_count,
                "total_scenarios": len(scenarios),
                "missing_negative": max(0, len(requirements) - negative_count),
            },
        )
        result.setdefault("obligation_coverage", self._obligation_coverage(obligations, scenarios))
        self.store_short_term_memory("coverage_gaps", result)
        return result

    def _obligation_coverage(self, obligations: List[Dict], scenarios: List[Dict]) -> List[Dict]:
        coverage = []
        scenario_by_ob: Dict[str, List[str]] = {}
        for sc in scenarios:
            for rid in sc.get("requirement_ids", []) + sc.get("obligation_ids", []):
                scenario_by_ob.setdefault(str(rid), []).append(str(sc.get("id") or ""))
            oid = sc.get("obligation_id") or sc.get("id")
            if oid:
                scenario_by_ob.setdefault(str(oid), []).append(str(sc.get("id") or ""))
            ext = sc.get("external_id")
            if ext:
                scenario_by_ob.setdefault(str(ext), []).append(str(sc.get("id") or ""))
        for ob in obligations:
            if not isinstance(ob, dict):
                continue
            ob_id = str(ob.get("id") or "")
            matched = scenario_by_ob.get(ob_id, [])
            coverage.append(
                {
                    "obligation_id": ob_id,
                    "covered": bool(matched),
                    "scenario_ids": matched,
                    "gap_reason": "" if matched else "Sin escenario asociado",
                }
            )
        return coverage

    def _rule_based(self, requirements: List[Dict]) -> Dict[str, Any]:
        seen: Set[str] = set()
        duplicates = []
        for req in requirements:
            desc = (req.get("description") or "").lower().strip()
            if desc in seen:
                duplicates.append(req.get("id", desc))
            seen.add(desc)
        return {"gaps": [], "duplicates": duplicates, "conflicts": [], "coverage_score": 0.85 if requirements else 0.0}
