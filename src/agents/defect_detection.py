"""Defect detection — consolidate QA signals + LLM triage."""
import json
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.agents.memory_bus import bus_for_run
from src.agents.token_policy import output_tokens
from src.reporting.error_sanitization import (
    consolidate_execution_failures,
    sanitize_playwright_error,
    validation_blocked_context,
)


class DefectDetectionAgent(BaseAgent):
    """Consolidate failures, coverage gaps and readiness issues into defects.

    Memory contract:
      Reads: execution_results, coverage, readiness, rca, healing, test_plan
      Writes: defects (also execution_key: defects when run_id present)
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

        execution = bundle.get("execution_results") or bundle.get("execution") or {}
        coverage = bundle.get("coverage") or context.get("coverage") or {}
        readiness = context.get("readiness") or {}
        rca = bundle.get("rca") or {}
        healing = bundle.get("healing") or {}
        test_plan = bundle.get("test_plan") or {}

        defects: List[Dict[str, Any]] = []
        seq = 1
        validation_ctx = validation_blocked_context(execution, rca)

        raw_failures = execution.get("failures") or []
        per_test_failures, infra_blocker = consolidate_execution_failures(
            raw_failures,
            rca=rca,
            execution=execution,
        )

        if infra_blocker:
            defects.append(
                {
                    "id": f"DEF-{seq:03d}",
                    **infra_blocker,
                }
            )
            seq += 1
        else:
            for failure in per_test_failures:
                defects.append(
                    {
                        "id": f"DEF-{seq:03d}",
                        "severity": "high",
                        "category": "test_failure",
                        "title": failure.get("title") or "Automated test failure",
                        "description": self._failure_message(failure),
                        "source": "playwright_execution",
                        "status": "open",
                        "requirement_ids": [],
                    }
                )
                seq += 1

        for gap in coverage.get("gaps") or coverage.get("coverage_gaps") or []:
            defects.append(
                {
                    "id": f"DEF-{seq:03d}",
                    "severity": "medium",
                    "category": "coverage_gap",
                    "title": self._coverage_gap_title(gap),
                    "description": self._coverage_gap_description(gap),
                    "source": "coverage_agent",
                    "status": "open",
                }
            )
            seq += 1

        for artifact in readiness.get("missing_artifacts") or []:
            defects.append(
                {
                    "id": f"DEF-{seq:03d}",
                    "severity": "critical",
                    "category": "readiness",
                    "title": f"Missing artifact: {artifact}",
                    "description": f"Required document type {artifact} not found in intake.",
                    "source": "readiness_agent",
                    "status": "open",
                }
            )
            seq += 1

        if (
            rca.get("root_cause")
            and execution.get("failed", 0)
            and not infra_blocker
        ):
            summary = rca.get("failure_summary") or {}
            defects.append(
                {
                    "id": f"DEF-{seq:03d}",
                    "severity": "high",
                    "category": rca.get("failure_category", "root_cause"),
                    "title": summary.get("title") if isinstance(summary, dict) else "Failure root cause",
                    "description": sanitize_playwright_error(str(rca.get("root_cause", ""))),
                    "source": "rca_agent",
                    "status": "open",
                    "confidence": rca.get("confidence"),
                }
            )
            seq += 1

        if healing.get("healed") and healing.get("changes_applied"):
            defects.append(
                {
                    "id": f"DEF-{seq:03d}",
                    "severity": "low",
                    "category": "healing_applied",
                    "title": "Automation self-healing applied",
                    "description": json.dumps(
                        healing.get("changes_applied", [])[:5], ensure_ascii=False
                    ),
                    "source": "self_healing_agent",
                    "status": "mitigated",
                }
            )
            seq += 1

        summary: Dict[str, Any] = {
            "defects": defects,
            "total": len(defects),
            "blocking": len([d for d in defects if d["severity"] in {"critical", "high"}]),
            "healing_applied": bool(healing.get("healed")),
            "execution_pass_rate": execution.get("pass_rate"),
            "automation_scenarios": len(test_plan.get("scenarios", [])),
            **validation_ctx,
            "suppressed_duplicate_failures": len(raw_failures) if infra_blocker else 0,
        }
        if validation_ctx.get("infrastructure_blocker"):
            summary["infrastructure_blocker"] = validation_ctx["infrastructure_blocker"]

        infra_note = ""
        if infra_blocker:
            infra_note = (
                f"\nInfrastructure blocker: ONE consolidated defect (DEF-001). "
                f"Suppressed {len(raw_failures)} duplicate Playwright environment failures. "
                "Do NOT emit per-test defects for browser/executable errors."
            )

        triage = await self.ask_json(
            f"""TASK: Enterprise defect triage for release decision.
Defects: {self.compact_json(defects)}
Execution: pass_rate={execution.get('pass_rate')} failed={execution.get('failed')}
Validation blocked by infrastructure: {validation_ctx.get('validation_blocked')}
RCA category: {rca.get('failure_category')}
{infra_note}

Rules:
- If validation_blocked is true, release_readiness must be "validation_blocked" (not "no_go" for product).
- Infrastructure defects are owned by ops; do not treat them as application bugs.
- Recommend fixing the automation environment before judging product quality.

Return JSON:
{{
  "recommendations": [string],
  "release_blocker": boolean,
  "release_readiness": "go"|"no_go"|"go_with_risk"|"validation_blocked",
  "priority_order": [string],
  "defects_enriched": [{{ "id": string, "severity": string, "owner": "qa"|"dev"|"product"|"ops" }}]
}}""",
            self._triage_fallback(summary, validation_ctx, infra_blocker),
            temperature=0.1,
            max_tokens=output_tokens(self.agent_name, item_count=len(defects)),
            use_fallback_on_failure=True,
        )
        summary["recommendations"] = triage.get("recommendations", [])
        summary["release_blocker"] = triage.get("release_blocker", summary["blocking"] > 0)
        summary["release_readiness"] = triage.get(
            "release_readiness",
            "validation_blocked" if validation_ctx.get("validation_blocked") else "go_with_risk",
        )
        summary["priority_order"] = triage.get("priority_order", [])
        summary["generation_mode"] = triage.get("generation_mode", "llm")
        if triage.get("defects_enriched"):
            summary["defects"] = self._merge_enriched(defects, triage["defects_enriched"])

        self.store_short_term_memory("defects", summary)
        if bus:
            bus.publish("defects", summary)
        return summary

    @staticmethod
    def _triage_fallback(
        summary: Dict[str, Any],
        validation_ctx: Dict[str, Any],
        infra_blocker: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if validation_ctx.get("validation_blocked") and infra_blocker:
            remediation = infra_blocker.get("remediation") or [
                "Align Playwright version with the Docker execution image.",
                "Re-run automation after environment remediation.",
            ]
            return {
                "recommendations": remediation,
                "release_blocker": True,
                "release_readiness": "validation_blocked",
                "priority_order": ["DEF-001"],
                "defects_enriched": [{"id": "DEF-001", "severity": "critical", "owner": "ops"}],
            }
        return {
            "recommendations": ["Review blocking defects before release"],
            "release_blocker": summary["blocking"] > 0,
            "release_readiness": "no_go" if summary["blocking"] > 0 else "go_with_risk",
            "priority_order": [d["id"] for d in summary.get("defects", [])],
            "defects_enriched": [],
        }

    @staticmethod
    def _merge_enriched(defects: List[Dict], enriched: List[Dict]) -> List[Dict]:
        by_id = {d["id"]: d for d in defects}
        for row in enriched:
            if row.get("id") in by_id:
                by_id[row["id"]].update({k: v for k, v in row.items() if k != "id"})
        return list(by_id.values())

    @staticmethod
    def _coverage_gap_title(gap: Any) -> str:
        if isinstance(gap, str):
            return f"Brecha de cobertura: {gap}"
        req = gap.get("requirement_id") or gap.get("id") or "desconocido"
        return f"Brecha de cobertura: {req}"

    @staticmethod
    def _coverage_gap_description(gap: Any) -> str:
        if isinstance(gap, str):
            return gap
        if not isinstance(gap, dict):
            return str(gap)
        parts: List[str] = []
        if gap.get("description"):
            parts.append(str(gap["description"]))
        if gap.get("suggested_scenario"):
            parts.append(f"Escenario sugerido: {gap['suggested_scenario']}")
        if gap.get("missing_scenarios"):
            parts.append("Faltan escenarios automatizados para este requisito.")
        if not parts:
            return json.dumps(gap, ensure_ascii=False)
        return " ".join(parts)

    @staticmethod
    def _failure_message(failure: Dict[str, Any]) -> str:
        err = failure.get("error")
        if isinstance(err, dict):
            raw = err.get("message") or err.get("stack") or str(err)
        else:
            raw = str(err or failure.get("title") or "Test failed")
        return sanitize_playwright_error(raw)
