"""Playwright validation agent — runs `npx playwright test --list` before execution."""
from pathlib import Path
from typing import Any, Dict

from src.agents.base import BaseAgent
from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.runtime_policy import use_mock_playwright_execution
from src.services.target_url import resolve_target_url


class PlaywrightValidationAgent(BaseAgent):
    """Validate generated Playwright project compiles via `playwright test --list`.

    Memory contract:
      Reads: generated_code, test_plan
      Writes: playwright_validation (also execution_key: playwright_validation when run_id present)
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        generated = context.get("generated_code") or self.read_memory("generated_code", default={})
        project_path = generated.get("project_path")
        if not project_path:
            return {
                "status": "validation_failed",
                "reason": "no_project_path",
                "passed": 0,
                "failed": 1,
            }

        project_dir = Path(project_path)
        base_url = resolve_target_url(context)
        run_id = context.get("execution_id") or context.get("run_id") or ""
        test_plan = context.get("test_plan") or self.read_memory("test_plan", default={})
        bank_portal = (
            test_plan.get("aut_profile") == "bank_portal"
            or (test_plan.get("suite") or {}).get("aut_profile") == "bank_portal"
        )

        if use_mock_playwright_execution():
            result = {
                "status": "completed",
                "validation": {"ok": True, "skipped": True, "reason": "mock_execution"},
                "passed": 1,
                "failed": 0,
            }
        else:
            generator = PlaywrightGeneratorAgent(self.memory)
            result = await generator.validate_generated_project(
                project_dir, base_url, run_id, bank_portal=bank_portal
            )

        self.store_short_term_memory("playwright_validation", result)
        exec_id = context.get("execution_id") or context.get("run_id")
        if exec_id:
            self.memory.write(self.memory.execution_key(exec_id, "playwright_validation"), result)
        return result
