"""Self-healing agent — locator fixes in pages/ driven by RCA."""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.agents.memory_bus import bus_for_run
from src.agents.token_policy import output_tokens, truncate_text


class SelfHealingAgent(BaseAgent):
    """Apply locator fixes to generated page objects based on RCA.

    Memory contract:
      Reads: rca, generated_code
      Writes: healing (also execution_key: healing when run_id present)
    """

    ALLOWED_DIRS = ("pages",)

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        run_id = context.get("run_id") or context.get("execution_id")
        bus = bus_for_run(run_id, self.memory) if run_id else None
        bundle = await bus.build_automation_bundle(context) if bus else context

        rca = bundle.get("rca") or {}
        project_path = context.get("project_path") or (bundle.get("generated_code") or {}).get("project_path")
        confidence_threshold = float(os.getenv("HEAL_CONFIDENCE_THRESHOLD", "0.55"))
        rca_confidence = float(rca.get("confidence", 0))

        if rca_confidence < confidence_threshold:
            return {
                "healed": False,
                "reason": "confidence_below_threshold",
                "confidence": rca_confidence,
                "changes": [],
            }

        page_snippets = self._read_all_pages(project_path)
        candidates = rca.get("healing_candidates") or []

        task = f"""TASK: Propose Playwright Page Object locator fixes ONLY (files under pages/).
Never modify tests/*.spec.ts or assertions.

Return JSON:
{{
  "healed": boolean,
  "confidence": number,
  "changes": [
    {{ "file": "pages/SomePage.ts", "old": "exact substring to replace", "new": "replacement line", "rationale": string }}
  ],
  "skipped": [{{ "file": string, "reason": string }}]
}}

RCA: {self.compact_json(rca)}
Healing candidates from RCA: {self.compact_json(candidates)}
Current page files: {self.compact_json(page_snippets)}

Rules:
- "old" must exist verbatim in the file content provided.
- Prefer getByTestId / getByRole over CSS.
- Maximum 5 changes.
"""
        result = await self.ask_json(
            task,
            self._fallback_from_rca(rca, page_snippets),
            temperature=0.05,
            max_tokens=output_tokens(self.agent_name, item_count=len(page_snippets) * 2),
            use_fallback_on_failure=True,
        )

        applied: List[Dict] = []
        if result.get("healed") and project_path:
            applied = self._apply_changes(Path(project_path), result.get("changes", []))
            result["changes_applied"] = applied
            result["healed"] = len(applied) > 0
            if applied:
                result["confidence"] = max(rca_confidence, float(result.get("confidence", 0.7)))

        self.store_short_term_memory("healing", result)
        if bus:
            bus.publish("healing", result)
            bus.publish_execution_namespace("healing", result)
        return result

    def _read_all_pages(self, project_path: str | None) -> Dict[str, str]:
        if not project_path:
            return {}
        pages_dir = Path(project_path) / "pages"
        if not pages_dir.is_dir():
            return {}
        return {
            f"pages/{p.name}": truncate_text(
                p.read_text(encoding="utf-8", errors="ignore"),
                agent_name=self.agent_name,
            )
            for p in sorted(pages_dir.glob("*.ts"))
        }

    def _fallback_from_rca(self, rca: Dict, pages: Dict[str, str]) -> Dict[str, Any]:
        candidates = rca.get("healing_candidates") or []
        changes = []
        for c in candidates[:3]:
            fname = c.get("file", "")
            if not fname.startswith("pages/"):
                fname = f"pages/{fname.split('/')[-1]}"
            if fname in pages and "locator(" in pages[fname]:
                changes.append(
                    {
                        "file": fname,
                        "old": "locator('body')",
                        "new": f"page.getByTestId('{c.get('locator_hint', 'main').strip('[]')}')",
                        "rationale": "RCA healing candidate",
                    }
                )
        return {"healed": bool(changes), "confidence": 0.72, "changes": changes, "skipped": []}

    def _apply_changes(self, project_dir: Path, changes: List[Dict]) -> List[Dict]:
        applied = []
        for change in changes:
            file_rel = change.get("file", "")
            if not any(file_rel.startswith(d) for d in self.ALLOWED_DIRS):
                continue
            path = project_dir / file_rel
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            old, new = change.get("old", ""), change.get("new", "")
            if old and old in content:
                path.write_text(content.replace(old, new, 1), encoding="utf-8")
                applied.append({**change, "status": "applied"})
            elif new and "getByTestId" in new:
                updated = re.sub(
                    r"this\.page\.locator\([^)]+\)",
                    new.replace("page.", "this.page."),
                    content,
                    count=1,
                )
                if updated != content:
                    path.write_text(updated, encoding="utf-8")
                    applied.append({**change, "status": "applied_heuristic"})
        return applied
