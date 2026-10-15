"""Failure RCA agent — Playwright failures with 5-Whys taxonomy."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.agents.memory_bus import bus_for_run
from src.agents.token_policy import output_tokens, truncate_text
from src.memory.enhanced_memory import MemoryTier
from src.reporting.infra_failures import detect_execution_infrastructure_blocker, strip_box_drawing
from src.reporting.error_sanitization import (
    is_infrastructure_failure,
    infrastructure_failure_summary,
    sanitize_playwright_error,
)


class FailureRCAAgent(BaseAgent):
    """Root-cause analysis for Playwright failures.

    Memory contract:
      Reads: execution_results, test_plan, generated_code
      Writes: rca (also execution_key: rca when run_id present)
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        run_id = context.get("run_id") or context.get("execution_id")
        bus = bus_for_run(run_id, self.memory) if run_id else None
        bundle = await bus.build_automation_bundle(context) if bus else context

        execution = bundle.get("execution_results") or {}
        failures = execution.get("failures", [])
        artifacts = execution.get("artifacts", {})
        evidence = execution.get("evidence", {})
        evidence_entries = self._load_evidence_entries(evidence.get("manifest_path"))
        test_plan = bundle.get("test_plan") or {}
        generated = bundle.get("generated_code") or {}
        project_path = context.get("project_path") or generated.get("project_path")

        page_snippets = self._read_page_snippets(project_path)
        stderr = truncate_text(execution.get("stderr", "") or "", agent_name=self.agent_name)

        task = f"""TASK: Root-cause analysis for Playwright E2E failures (enterprise QA).

Return JSON:
{{
  "root_cause": string,
  "confidence": number,
  "failure_category": "locator"|"timing"|"environment"|"test_data"|"application_bug"|"test_bug"|"infra",
  "failure_summary": {{ "title": string, "severity": "critical"|"high"|"medium"|"low", "affected_tests": [string] }},
  "likely_root_cause": {{ "description": string, "confidence": number, "category": string, "evidence": [string] }},
  "five_whys": [string],
  "action_items": [{{ "priority": "P0"|"P1"|"P2", "action": string, "owner": "qa"|"dev"|"ops", "status": "pending" }}],
  "healing_candidates": [{{ "file": string, "locator_hint": string, "confidence": number }}]
}}

Failures: {self.compact_json(failures)}
Executed tests: {self.compact_json(execution.get('executed_tests', []))}
Artifacts: {self.compact_json(artifacts)}
Evidence manifest (step screenshots): {self.compact_json(evidence_entries)}
Evidence PDF: {evidence.get('pdf_path', 'none')}
Stderr excerpt: {json.dumps(stderr)}
Test plan scenarios: {self.compact_json(test_plan.get('scenarios', []))}
Page object snippets: {self.compact_json(page_snippets)}

HISTORICAL KNOWLEDGE:
{bundle.get("knowledge_context") or "None"}
"""
        result = await self.ask_json(
            task,
            self._fallback(failures, execution),
            temperature=0.1,
            max_tokens=output_tokens(self.agent_name, item_count=len(failures) * 2),
            use_fallback_on_failure=True,
        )

        if "root_cause" not in result and result.get("likely_root_cause"):
            result["root_cause"] = result["likely_root_cause"].get("description", "Unknown")
        result.setdefault("confidence", result.get("likely_root_cause", {}).get("confidence", 0.5))
        result.setdefault("generation_mode", "llm")
        result["analysis_metadata"] = {
            "failures_analyzed": len(failures),
            "traces_analyzed": len(artifacts.get("traces", [])),
            "evidence_screenshots": len(self._load_evidence_entries(evidence.get("manifest_path"))),
            "pages_reviewed": list(page_snippets.keys()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self.write_memory("rca", result, tier=MemoryTier.WORKING, tags=["rca", "automation"])
        if bus:
            bus.publish("rca", result)
            bus.publish_execution_namespace("rca", result)
        return result

    @staticmethod
    def _load_evidence_entries(manifest_path: str | None) -> List[Dict]:
        if not manifest_path:
            return []
        path = Path(manifest_path)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("entries", [])
        except (json.JSONDecodeError, OSError):
            return []

    def _read_page_snippets(self, project_path: str | None) -> Dict[str, str]:
        if not project_path:
            return {}
        pages_dir = Path(project_path) / "pages"
        if not pages_dir.is_dir():
            return {}
        snippets = {}
        for path in sorted(pages_dir.glob("*.ts")):
            if path.name == "BasePage.ts":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            snippets[path.name] = truncate_text(text, agent_name=self.agent_name)
        return snippets

    def _fallback(self, failures: List[Dict], execution: Dict) -> Dict[str, Any]:
        if not failures and execution.get("failed", 0) == 0:
            return {
                "root_cause": "No failures detected",
                "confidence": 0.9,
                "failure_category": "test_bug",
                "failure_summary": {"title": "All passed", "severity": "low", "affected_tests": []},
                "five_whys": [],
                "action_items": [],
                "healing_candidates": [],
            }
        infra = detect_execution_infrastructure_blocker(execution)
        if infra:
            return {
                "root_cause": strip_box_drawing(str(infra.get("description") or "")),
                "confidence": 0.95,
                "failure_category": "infra",
                "failure_summary": {
                    "title": infra.get("title", "Infrastructure failure"),
                    "severity": "critical",
                    "affected_tests": list(
                        {
                            f.get("title")
                            for f in failures
                            if isinstance(f, dict) and f.get("title")
                        }
                    )[:12],
                },
                "likely_root_cause": {
                    "description": strip_box_drawing(str(infra.get("description") or "")),
                    "confidence": 0.95,
                    "category": "infra",
                    "evidence": infra.get("remediation_steps") or [],
                },
                "five_whys": [
                    "Playwright could not launch browsers in the execution container",
                    "Installed Playwright version does not match bundled browser binaries",
                    "Docker base image is stale relative to project dependencies",
                    "CI/runtime did not rebuild the image after dependency upgrades",
                    "Update the Playwright Docker image and redeploy before functional QA",
                ],
                "action_items": [
                    {
                        "priority": "P0",
                        "action": step,
                        "owner": "ops",
                        "status": "pending",
                    }
                    for step in (infra.get("remediation_steps") or [])[:4]
                ],
                "healing_candidates": [],
            }
        err = failures[0].get("error", {}) if failures else {}
        msg = err.get("message", "Test assertion or locator failure") if isinstance(err, dict) else str(err)
        msg = sanitize_playwright_error(str(msg))
        title = failures[0].get("title", "Test failure") if failures else "Unknown"
        msg_lower = str(msg).lower()
        stderr = str(execution.get("stderr") or "")
        if is_infrastructure_failure(msg, stderr=stderr, failures=failures):
            summary = infrastructure_failure_summary(
                msg or stderr,
                affected_count=len(failures),
            )
            affected = [str(f.get("title") or "") for f in failures[:12] if f.get("title")]
            return {
                "root_cause": summary["description"],
                "confidence": 0.95,
                "failure_category": "infra",
                "failure_summary": {
                    "title": summary["title"],
                    "severity": "critical",
                    "affected_tests": affected,
                },
                "likely_root_cause": {
                    "description": summary["description"],
                    "confidence": 0.95,
                    "category": "infra",
                    "evidence": summary["remediation"][:3],
                },
                "five_whys": [
                    "Playwright could not launch the browser in the execution environment",
                    "Browser executable path/version does not match installed Playwright",
                    "Docker image or PLAYWRIGHT_BROWSERS_PATH is out of sync with project Playwright version",
                    "npm install updated Playwright without updating the runtime image",
                    "Ops must align container image and browser cache before functional validation",
                ],
                "action_items": [
                    {
                        "priority": "P0",
                        "action": action,
                        "owner": "ops",
                        "status": "pending",
                    }
                    for action in summary["remediation"][:3]
                ],
                "healing_candidates": [],
            }
        is_compile = (
            (isinstance(err, dict) and err.get("category") == "compile")
            or "syntaxerror" in msg_lower
            or "has already been declared" in msg_lower
            or execution.get("compile_errors")
        )
        if is_compile:
            return {
                "root_cause": str(msg),
                "confidence": 0.85,
                "failure_category": "compile",
                "failure_summary": {"title": title, "severity": "critical", "affected_tests": [title]},
                "likely_root_cause": {
                    "description": "Generated Playwright specs failed TypeScript compile (imports or syntax)",
                    "confidence": 0.85,
                    "category": "compile",
                    "evidence": [str(msg)[:500]],
                },
                "five_whys": [
                    "Playwright could not load or compile test files",
                    "Invalid or duplicate imports in generated specs",
                    "LLM codegen mapped fixture paths to wrong exports",
                    "Template fallback was not applied before execution",
                    "Regenerate specs from obligation templates with empty LLM hints",
                ],
                "action_items": [
                    {
                        "priority": "P0",
                        "action": "Regenerate bank portal specs from templates (empty codegen hints)",
                        "owner": "qa",
                        "status": "pending",
                    },
                ],
                "healing_candidates": [
                    {"file": "tests/", "locator_hint": "template_spec_regeneration", "confidence": 0.9, "category": "compile"},
                ],
            }
        return {
            "root_cause": str(msg),
            "confidence": 0.68,
            "failure_category": "locator",
            "failure_summary": {"title": title, "severity": "high", "affected_tests": [title]},
            "likely_root_cause": {
                "description": "Element not found or timeout — selector drift or slow load",
                "confidence": 0.68,
                "category": "locator",
                "evidence": [str(msg)[:500]],
            },
            "five_whys": [
                "Test failed on assertion/locator",
                "UI element not interactable in time",
                "Selector may not match current DOM",
                "Application or test data changed",
                "Locator strategy needs hardening (data-testid)",
            ],
            "action_items": [
                {"priority": "P0", "action": "Update page object locator per healing_candidates", "owner": "qa", "status": "pending"},
            ],
            "healing_candidates": [
                {"file": "pages/LoginPage.ts", "locator_hint": "[data-testid]", "confidence": 0.7},
            ],
        }
