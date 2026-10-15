"""Playwright test plan architect — enterprise POM + locator strategy."""
import json
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.agents.token_policy import output_tokens, prompt_budget
from src.agents.memory_bus import bus_for_run
from src.automation.scenario_coverage import build_scenarios_from_obligations, merge_scenario_coverage


class PlaywrightArchitectAgent(BaseAgent):
    """Design Playwright test plan (POM + scenarios) from functional scenarios.

    Memory contract:
      Reads: functional_scenarios, requirements, traceability_matrix, interpreted_intake,
             site_exploration
      Writes: test_plan (also execution_key: test_plan when run_id present)
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        run_id = context.get("run_id") or context.get("execution_id")
        bus = bus_for_run(run_id, self.memory) if run_id else None
        bundle = context
        if bus:
            try:
                bundle = await bus.build_automation_bundle(context)
            except Exception:
                bundle = context

        scenarios = bundle.get("functional_scenarios") or []
        requirements = bundle.get("requirements") or []
        traceability = bundle.get("traceability_matrix") or []
        target_url = bundle.get("target_url") or "https://example.com"
        from src.services.target_url import resolve_target_url

        resolved = resolve_target_url(context)
        if resolved:
            target_url = resolved
        project_id = bundle.get("project_id", "demo")
        asset_bindings = bundle.get("asset_bindings") or context.get("interpreted_intake", {}).get("asset_bindings") or []
        interpreted = bundle.get("interpreted_intake") or context.get("interpreted_intake") or {}
        obligations = interpreted.get("obligations") or []

        from src.automation.bank_portal import is_bank_portal_context, pom_structure, suite_meta

        bank_portal = is_bank_portal_context(
            context, obligations=obligations, target_url=target_url
        )
        obligation_scenarios = build_scenarios_from_obligations(
            obligations,
            target_url,
            bank_portal=bank_portal,
            include_manual=True,
        )
        if bank_portal:
            from src.automation.obligation_dedup import dedupe_obligations
            from src.automation.obligation_steps import (
                automatable_obligations,
                map_functional_scenario_to_scenario,
            )

            obligations = dedupe_obligations(obligations)
            obligation_scenarios = build_scenarios_from_obligations(
                obligations,
                target_url,
                bank_portal=True,
                include_manual=True,
            )
            negative_scenarios = [
                map_functional_scenario_to_scenario(s, target_url)
                for s in scenarios
                if isinstance(s, dict)
                and (s.get("parent_scenario_id") or s.get("scenario_type") not in (None, "happy_path"))
                and not s.get("playwright_skip")
            ]
            combined = obligation_scenarios + negative_scenarios
            result = self._fallback(
                scenarios,
                target_url,
                obligations=obligations,
                bank_portal=True,
                obligation_scenarios=combined,
            )
            result["target_url"] = target_url
            result["aut_profile"] = "bank_portal"
            result["obligation_coverage"] = {
                "obligations": len(obligations),
                "scenarios": len(result.get("scenarios") or []),
                "complete": len(result.get("scenarios") or []) >= len(automatable_obligations(obligations)),
                "negative_scenarios": len(negative_scenarios),
            }
            self.store_short_term_memory("test_plan", result)
            if bus:
                bus.publish("test_plan", result)
                bus.publish_execution_namespace("test_plan", result)
            exec_id = context.get("execution_id") or context.get("run_id")
            if exec_id:
                self.memory.write(self.memory.execution_key(exec_id, "test_plan"), result)
            return result

        from src.automation.obligation_steps import automatable_obligations
        bank_pom = pom_structure() if bank_portal else {}
        bank_rules = ""
        if bank_portal:
            bank_rules = """
BANK PORTAL RULES (INC1353166):
- Use data-testid locators from bank portal: login-cliente, login-documento, login-clave, login-token, login-submit,
  crear-digitar, crear-importar-archivo, validar-page, actualizar-page, consultas-page, buscar-procesos.
- First step of each UI scenario: action "login" with data "login_user" (uses bank-portal-auth fixture).
- Routes: /login, /inscripciones/crear, /inscripciones/validar, /inscripciones/actualizar, /consultas.
- Actions: goto, click, fill, login, login_negative, testid_click, testid_fill, testid_visible, upload, wait,
  demo_evaluate, seed_inscripcion, click_first_testid.
- Scenarios with automation_tier manual must set "manual": true only when portal cannot simulate (rare).
- Business-day (días hábiles) scenarios use demo_evaluate / seedEnviadoOtroBanco / triggerBusinessDayRejections.
- Tag each scenario with EMPRESARIAL-xxxxx when external_id is present.
"""

        task = f"""TAREA: Diseñar plan de automatización E2E con Playwright (Page Object Model) para el proyecto {project_id}.
URL de la aplicación objetivo: {target_url}
Modo portal bancario: {bank_portal}
Asset bindings (usar bank-portal-auth / dynamic-auth — nunca hardcodear secrets):
{self.compact_json(asset_bindings)}
{bank_rules}

Devuelve JSON con esta estructura exacta:
{{
  "suite": {{ "name": string, "feature": string, "browser_matrix": ["chromium"] }},
  "pom_structure": {{
    "pages": [{{ "name": string, "route": string, "responsibilities": [string] }}],
    "fixtures": [string]
  }},
  "scenarios": [
    {{
      "id": string,
      "name": string,
      "priority": "P0"|"P1"|"P2",
      "page_object": string,
      "requirement_ids": [string],
      "acceptance_criteria_ids": [string],
      "preconditions": [string],
      "evidence_steps": [string],
      "steps": [{{ "id": string, "action": string, "target": string, "data": string, "capture_evidence": boolean }}],
      "assertions": [{{ "type": "url"|"visible"|"text"|"count", "target": string, "expected": string }}],
      "locators": {{ "primary": string, "fallback": string }},
      "tags": ["smoke"|"regression"]
    }}
  ],
  "test_data": {{ "valid_user": {{}}, "invalid_cases": [] }},
  "non_functional": {{ "max_load_ms": number, "retry_policy": string }}
}}

Reglas:
- DEBES incluir un escenario por cada id de obligación listado en OBLIGACIONES (mismo conteo, preservar ids).
- No omitir ni fusionar obligaciones — solo refinar pasos/locators.
- Mapear cada escenario P0 a al menos un requirement_id.
- Locators preferir data-testid, luego getByRole, luego accessible name — nunca cadenas CSS frágiles.
- Pasos deben ser implementables en Playwright (goto, click, fill, expect).
- Cada paso necesita un "id" único (p. ej. step-01-goto). Set capture_evidence=true para acciones visuales (goto, click, fill) y después de assertions clave.
- evidence_steps lista los step ids que deben producir screenshots de ejecución (subconjunto de step ids).
- Escenarios con automation_tier manual deben set "manual": true (documentado en el plan; Playwright emitirá test.skip).

OBLIGACIONES ({len(obligations)} — cobertura completa requerida):
{self.compact_json(obligations, max_chars=prompt_budget(self.agent_name, len(obligations) * 500))}

ESCENARIOS FUNCIONALES:
{self.compact_json(scenarios)}

REQUISITOS:
{self.compact_json(requirements)}

TRAZABILIDAD:
{self.compact_json(traceability)}

CONOCIMIENTO HISTÓRICO (pgvector):
{bundle.get("knowledge_context") or "None"}
"""
        result = await self.ask_json(
            task,
            self._fallback(
                scenarios,
                target_url,
                obligations=obligations,
                bank_portal=bank_portal,
                obligation_scenarios=obligation_scenarios,
            ),
            temperature=0.1,
            use_fallback_on_failure=True,
            max_tokens=output_tokens(
                self.agent_name,
                item_count=max(len(obligations), len(scenarios), len(requirements)),
            ),
        )

        result.setdefault("suite", suite_meta() if bank_portal else {"name": f"{project_id}-e2e", "feature": "core", "browser_matrix": ["chromium"]})
        result.setdefault("pom_structure", bank_pom if bank_portal else {"pages": [], "fixtures": ["test-fixtures"]})
        result.setdefault("scenarios", [])
        result.setdefault("test_data", {})
        result["target_url"] = target_url
        result["aut_profile"] = "bank_portal" if bank_portal else result.get("aut_profile")
        llm_scenarios = [self._normalize_scenario(sc) for sc in result.get("scenarios", [])]
        baseline = obligation_scenarios or self._fallback(
            scenarios,
            target_url,
            obligations=obligations,
            bank_portal=bank_portal,
            obligation_scenarios=obligation_scenarios,
        ).get("scenarios", [])
        result["scenarios"] = [
            self._normalize_scenario(sc)
            for sc in merge_scenario_coverage(baseline, llm_scenarios)
        ]
        result["obligation_coverage"] = {
            "obligations": len(obligations),
            "scenarios": len(result["scenarios"]),
            "complete": len(result["scenarios"]) >= len(obligations),
        }

        self.store_short_term_memory("test_plan", result)
        if bus:
            bus.publish("test_plan", result)
            bus.publish_execution_namespace("test_plan", result)
        exec_id = context.get("execution_id") or context.get("run_id")
        if exec_id:
            self.memory.write(self.memory.execution_key(exec_id, "test_plan"), result)
        return result

    @staticmethod
    def _should_capture_evidence(action: str) -> bool:
        return action.lower() in {
            "goto", "click", "fill", "wait", "assert", "expect",
            "login", "testid_click", "testid_fill", "testid_visible", "upload",
            "seed_inscripcion", "click_first_testid", "demo_evaluate", "login_negative",
        }

    def _normalize_step(self, step: Dict, index: int) -> Dict:
        action = (step.get("action") or "wait").lower()
        step_id = step.get("id") or f"step-{index + 1:02d}-{action}"
        capture = step.get("capture_evidence")
        if capture is None:
            capture = self._should_capture_evidence(action)
        return {
            **step,
            "id": step_id,
            "action": action,
            "capture_evidence": bool(capture),
        }

    def _normalize_scenario(self, scenario: Dict) -> Dict:
        steps = [self._normalize_step(s, i) for i, s in enumerate(scenario.get("steps") or [])]
        evidence_steps = scenario.get("evidence_steps")
        if not evidence_steps:
            evidence_steps = [s["id"] for s in steps if s.get("capture_evidence")]
        for step in steps:
            if step["id"] in evidence_steps:
                step["capture_evidence"] = True
        return {**scenario, "steps": steps, "evidence_steps": evidence_steps}

    def _fallback(
        self,
        scenarios: List[Dict],
        target_url: str,
        *,
        obligations: Optional[List[Dict]] = None,
        bank_portal: bool = False,
        obligation_scenarios: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        from src.automation.bank_portal import pom_structure, suite_meta

        if obligation_scenarios:
            mapped = list(obligation_scenarios)
        elif obligations:
            mapped = build_scenarios_from_obligations(
                obligations,
                target_url,
                bank_portal=bank_portal,
                include_manual=True,
            )
        else:
            mapped = [self._map_functional_scenario(s, target_url, i) for i, s in enumerate(scenarios)]

        if bank_portal and mapped:
            return {
                "suite": suite_meta(),
                "pom_structure": pom_structure(),
                "scenarios": mapped,
                "test_data": {},
                "target_url": target_url,
                "aut_profile": "bank_portal",
            }
        if not mapped:
            mapped.append(
                {
                    "id": "E2E-001",
                    "name": "smoke-home",
                    "priority": "P0",
                    "page_object": "HomePage",
                    "requirement_ids": [],
                    "evidence_steps": ["step-01-goto"],
                    "steps": [{"id": "step-01-goto", "action": "goto", "target": "BASE_URL", "data": target_url, "capture_evidence": True}],
                    "assertions": [{"type": "visible", "target": "body", "expected": "true"}],
                    "locators": {"primary": "body", "fallback": "html"},
                    "tags": ["smoke"],
                }
            )
        return {
            "suite": {"name": "aptp-suite", "feature": "core", "browser_matrix": ["chromium"]},
            "pom_structure": {"pages": [{"name": "HomePage", "route": "/", "responsibilities": ["load"]}], "fixtures": []},
            "scenarios": mapped,
            "test_data": {},
            "target_url": target_url,
        }

    def _map_functional_scenario(self, s: Dict, target_url: str, index: int) -> Dict:
        name = (s.get("title") or s.get("name") or f"scenario-{index}").replace(" ", "-").lower()
        return {
            "id": s.get("id", f"E2E-{index+1:03d}"),
            "name": name,
            "priority": "P1" if index == 0 else "P2",
            "page_object": "".join(w.title() for w in name.split("-")) + "Page",
            "requirement_ids": s.get("requirement_ids", []),
            "acceptance_criteria_ids": [],
            "preconditions": ["Application is reachable"],
            "evidence_steps": ["step-01-goto", "step-02-wait"],
            "steps": [
                {"id": "step-01-goto", "action": "goto", "target": "BASE_URL", "data": target_url, "capture_evidence": True},
                {"id": "step-02-wait", "action": "wait", "target": "networkidle", "data": "", "capture_evidence": True},
            ],
            "assertions": [{"type": "url", "target": "page", "expected": target_url}],
            "locators": {"primary": "[data-testid=main]", "fallback": "role=main"},
            "tags": ["smoke" if index == 0 else "regression"],
        }
