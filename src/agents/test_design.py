"""Functional test design agent."""
import logging
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.agents.memory_bus import _requirements_list
from src.agents.token_policy import output_tokens
from src.automation.negative_scenarios import populate_negative_paths, synthesize_negative_scenarios

logger = logging.getLogger("test_design")


class TestDesignAgent(BaseAgent):
    """Design functional scenarios (happy + negative paths) from obligations.

    Memory contract:
      Reads: interpreted_intake, requirements, coverage_gaps, site_exploration
      Writes: functional_scenarios
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        interpreted = context.get("interpreted_intake") or self.read_memory("interpreted_intake", {})
        obligations = interpreted.get("obligations") or []
        requirements = _requirements_list(
            context.get("requirements") or self.read_memory("requirements", default=[])
        )
        coverage = context.get("coverage") or self.read_memory("coverage", default={})
        acceptance = context.get("acceptance_criteria") or []

        if obligations:
            from src.automation.obligation_steps import automatable_obligations

            automatable = automatable_obligations(obligations)
            manual_count = len(obligations) - len(automatable)
            fallback = {
                "functional_scenarios": self._scenarios_from_obligations(obligations),
                "coverage_targets": [],
                "fallback_used": True,
                "manual_obligations_skipped": manual_count,
            }
            task = f"""TAREA: Diseñar escenarios funcionales de prueba 1:1 desde obligaciones de prueba.
Incluir obligaciones de días hábiles — el portal demo simula calendario sin esperas reales.
NO diseñar automatización Playwright para obligaciones manual (solo documentarlas).

Para CADA obligación automatizable define también negative_paths[] con variaciones adversas.

Devuelve JSON con functional_scenarios usando ids estables que coincidan con ids de obligación (OBL-xxx).
Cada escenario debe incluir: scenario_type (happy_path|negative|boundary|resilience|security),
negative_paths[], test_types[], risk_reason, input_variation, expected_failure_mode, recovery_expectation.
Conserva external_id (EMPRESARIAL-xxxxx) en tags cuando exista.
Todos los textos en español.

OBLIGACIONES (automatizables): {self.compact_json(automatable)}
MANUALES OMITIDAS DE AUTOMATIZACIÓN: {manual_count}
EXPLORACIÓN DEL SITIO: {self.compact_json(context.get("site_exploration") or self.read_memory("site_exploration", default={}))}
"""
            result = await self.ask_json(task, fallback, temperature=0.15, max_tokens=output_tokens(self.agent_name, item_count=len(obligations)), use_fallback_on_failure=True)
        else:
            fallback = {
                "functional_scenarios": self._fallback_scenarios(requirements),
                "coverage_targets": [],
                "fallback_used": True,
            }

            task = f"""TASK: Design functional test scenarios (ISTQB) — NOT automation code.

Return JSON:
{{
  "functional_scenarios": [
    {{
      "id": string,
      "title": string,
      "priority": "P0"|"P1"|"P2",
      "requirement_ids": [string],
      "preconditions": [string],
      "steps": [string],
      "expected_results": [string],
      "negative_paths": [string],
      "scenario_type": "happy_path"|"negative"|"boundary"|"resilience"|"security",
      "test_types": ["smoke"|"regression"|"boundary"|"negative"]
    }}
  ],
  "coverage_targets": [string]
}}

Rules:
- Cover all critical/high requirements with at least one scenario each when feasible.
- For EVERY happy-path scenario, also design negative, boundary, and resilience variants where applicable.
- Include negative and boundary cases for contact forms, navigation, validation, auth, and business rules.
- Use stable ids (TC-HERO-001, TC-CONTACT-001, etc.).

REQUIREMENTS: {self.compact_json(requirements)}
COVERAGE ANALYSIS: {self.compact_json(coverage)}
ACCEPTANCE CRITERIA: {self.compact_json(acceptance)}
SITE EXPLORATION: {self.compact_json(context.get("site_exploration") or self.read_memory("site_exploration", default={}))}
"""
            result = await self.ask_json(
                task,
                fallback,
                temperature=0.15,
                max_tokens=output_tokens(self.agent_name, item_count=len(requirements)),
                use_fallback_on_failure=True,
                max_attempts=3,
            )

        if result.get("fallback_used"):
            logger.warning(
                "TestDesignAgent used requirement-derived fallback (%d scenarios)",
                len(result.get("functional_scenarios") or []),
            )

        result.setdefault("functional_scenarios", [])
        result.setdefault("coverage_targets", [])
        if obligations:
            from src.automation.obligation_steps import automatable_obligations
            from src.intake.test_naming import assign_case_codes

            obligations = assign_case_codes([o for o in obligations if isinstance(o, dict)])
            result["functional_scenarios"] = self._merge_manual_obligation_scenarios(
                result.get("functional_scenarios") or [],
                obligations,
            )

        site_exploration = context.get("site_exploration") or self.read_memory("site_exploration", default={})
        from src.automation.bank_portal import is_bank_portal_context

        bank_portal = is_bank_portal_context(
            context,
            obligations=obligations,
            target_url=(context.get("interpreted_intake") or {}).get("environment", {}).get("target_url", ""),
        )
        if bank_portal and obligations:
            result["functional_scenarios"] = self._ensure_demo_module_scenarios(
                result.get("functional_scenarios") or [], obligations, site_exploration
            )
        base_scenarios = [
            populate_negative_paths(s) if isinstance(s, dict) else s
            for s in result.get("functional_scenarios") or []
        ]
        expanded = synthesize_negative_scenarios(
            base_scenarios,
            site_exploration=site_exploration,
        )
        if bank_portal:
            from src.automation.obligation_steps import map_functional_scenario_to_scenario

            target_url = (context.get("interpreted_intake") or {}).get("environment", {}).get("target_url", "")
            for scenario in expanded:
                if scenario.get("automation_tier") == "manual" or scenario.get("manual"):
                    scenario["playwright_skip"] = True
            playwright_scenarios = [
                map_functional_scenario_to_scenario(sc, target_url)
                for sc in expanded
                if isinstance(sc, dict) and not sc.get("playwright_skip")
            ]
            result["playwright_scenarios"] = playwright_scenarios
        else:
            for scenario in expanded:
                if scenario.get("parent_scenario_id") or scenario.get("scenario_type") != "happy_path":
                    scenario["playwright_skip"] = True
        result["functional_scenarios"] = expanded
        result["negative_scenario_count"] = sum(
            1 for s in result["functional_scenarios"] if s.get("scenario_type") != "happy_path"
        )
        self.store_short_term_memory("functional_scenarios", result["functional_scenarios"])
        return result

    def _fallback_scenarios(self, requirements: List[Dict[str, Any]]):
        scenarios = [
            {
                "id": f"TC-{i+1:03d}",
                "title": f"Verify {r.get('description', 'requirement')[:120]}",
                "priority": "P1" if i < 3 else "P2",
                "steps": ["Given system ready", "When user performs action", "Then outcome matches AC"],
                "expected_results": ["Behavior matches requirement"],
                "requirement_ids": [r.get("id", f"REQ-{i+1}")],
                "negative_paths": ["invalid_input", "missing_required_field"],
                "scenario_type": "happy_path",
                "test_types": ["smoke" if i == 0 else "regression"],
            }
            for i, r in enumerate(requirements)
        ]
        return synthesize_negative_scenarios(scenarios)

    def _merge_manual_obligation_scenarios(
        self,
        scenarios: List[Dict[str, Any]],
        obligations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        allowed_ids = {o.get("id") for o in obligations if isinstance(o, dict)}
        filtered = [
            s for s in scenarios if isinstance(s, dict) and s.get("id") in allowed_ids
        ]
        by_id = {str(s.get("id")): s for s in filtered if s.get("id")}
        merged: List[Dict[str, Any]] = []
        for ob in obligations:
            if not isinstance(ob, dict):
                continue
            oid = str(ob.get("id") or "")
            existing = by_id.get(oid)
            if existing:
                existing.setdefault("cp_code", ob.get("cp_code"))
                existing.setdefault("automation_tier", ob.get("automation_tier"))
                if ob.get("automation_tier") == "manual":
                    existing["playwright_skip"] = True
                    existing["manual"] = True
                merged.append(existing)
            elif ob.get("automation_tier") == "manual":
                merged.append(
                    {
                        "id": oid,
                        "title": ob.get("title", ""),
                        "cp_code": ob.get("cp_code"),
                        "priority": "P2",
                        "preconditions": ob.get("preconditions") or [],
                        "steps": ob.get("steps") or [],
                        "expected_results": ob.get("expected_results") or [],
                        "requirement_ids": [f"REQ-{oid}"],
                        "automation_tier": "manual",
                        "playwright_skip": True,
                        "manual": True,
                        "scenario_type": "happy_path",
                        "external_id": ob.get("external_id", ""),
                    }
                )
            elif oid in allowed_ids:
                merged.append(by_id.get(oid) or {})
        return merged or filtered

    def _scenarios_from_obligations(self, obligations: List[Dict[str, Any]]):
        from src.automation.bank_portal import is_bank_portal_context
        from src.intake.test_naming import assign_case_codes

        obligations = assign_case_codes([o for o in obligations if isinstance(o, dict)])
        bank = is_bank_portal_context(obligations=obligations)
        scenarios = []
        for ob in obligations:
            tier = ob.get("automation_tier", "partial")
            priority = "P0" if tier == "automated" else ("P2" if tier == "manual" else "P1")
            scenario = populate_negative_paths({
                "id": ob.get("id", f"OBL-{len(scenarios)+1:03d}"),
                "title": ob.get("title", "Obligación"),
                "cp_code": ob.get("cp_code"),
                "priority": priority,
                "preconditions": ob.get("preconditions") or [],
                "steps": ob.get("steps") or ["Execute obligation flow"],
                "expected_results": ob.get("expected_results") or ["Expected behavior verified"],
                "requirement_ids": [f"REQ-{ob.get('id', '')}"],
                "test_types": ["smoke" if tier == "automated" else "regression"],
                "automation_tier": tier,
                "required_assets": ob.get("required_assets") or [],
                "external_id": ob.get("external_id", ""),
            })
            if tier == "manual":
                scenario["playwright_skip"] = True
                scenario["manual"] = True
            if bank:
                scenario["module"] = ob.get("module") or None
                scenario["aut_profile"] = "bank_portal"
            scenarios.append(scenario)
        if bank:
            return scenarios
        return synthesize_negative_scenarios(scenarios)

    def _ensure_demo_module_scenarios(
        self,
        scenarios: List[Dict[str, Any]],
        obligations: List[Dict[str, Any]],
        site_exploration: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Ensure bank portal demo modules are represented even when intake is sparse."""
        from src.automation.obligation_steps import detect_module

        modules = {"login", "crear", "validar", "actualizar", "consultas", "pagos", "transferencias"}
        seen = set()
        for ob in obligations:
            if isinstance(ob, dict):
                seen.add(detect_module(ob))
        for sc in scenarios:
            if isinstance(sc, dict):
                seen.add(detect_module(sc))

        out = list(scenarios)
        next_idx = len(scenarios) + 1
        for module in modules - seen:
            module_obligation = {
                "id": f"OBL-DEMO-{module.upper()}",
                "title": f"Flujo demo del módulo {module}",
                "automation_tier": "automated",
                "preconditions": ["Usuario autenticado en portal demo"],
                "steps": [f"Ejecutar flujo representativo del módulo {module}"],
                "expected_results": [f"El módulo {module} responde según expectativa del demo"],
                "module": module,
                "aut_profile": "bank_portal",
            }
            out.append(populate_negative_paths(module_obligation))
            next_idx += 1
        return out
