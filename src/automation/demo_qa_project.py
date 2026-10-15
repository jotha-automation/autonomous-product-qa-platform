"""Keep DEMO-QA Playwright project aligned with deterministic obligation steps."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "src" / "agents" / "templates" / "playwright"


def reconcile_demo_qa_project(
    project_dir: Path,
    test_plan: Dict[str, Any],
    *,
    target_url: str,
    run_id: str = "",
) -> List[str]:
    """Rewrite DEMO-QA specs from deterministic obligation steps (post-codegen guard)."""
    from src.agents.playwright_generator import PlaywrightGeneratorAgent
    from src.automation.obligation_steps import map_obligation_to_scenario

    project_dir = Path(project_dir)
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    written: List[str] = []
    planned: set[str] = set()

    scenarios: List[Dict[str, Any]] = []
    for raw in test_plan.get("scenarios") or []:
        if raw.get("parent_scenario_id") or raw.get("playwright_skip"):
            continue
        scenarios.append(
            map_obligation_to_scenario(
                {
                    "id": raw.get("id"),
                    "title": raw.get("name") or raw.get("title"),
                    "external_id": raw.get("external_id"),
                    "steps": raw.get("steps") or [],
                    "expected_results": [],
                    "preconditions": raw.get("preconditions") or [],
                    "automation_tier": raw.get("automation_tier", "automated"),
                },
                target_url,
            )
        )

    tests_dir = project_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for scenario in scenarios:
        name = agent._slug_for_spec(scenario.get("name", "scenario"))
        page_class = scenario.get("page_object") or "ConsultasPage"
        spec_rel = f"{name}.spec.ts"
        planned.add(spec_rel)
        spec_path = tests_dir / spec_rel
        spec_content = agent._generate_spec(scenario, page_class, name, {}, target_url, bank_portal=True)
        spec_path.write_text(spec_content, encoding="utf-8")
        rel = f"tests/{spec_rel}"
        if rel not in written:
            written.append(rel)

    for stale in tests_dir.glob("*.spec.ts"):
        if stale.name not in planned:
            stale.unlink()
    for stale in tests_dir.glob("obl-emp-*.spec.ts"):
        stale.unlink()

    for rel in (
        "playwright.config.ts",
        "pages/LoginPage.ts",
        "fixtures/evidence.ts",
        "fixtures/dynamic-auth.ts",
        "fixtures/video-evidence-reporter.ts",
    ):
        src = _TEMPLATES / rel
        if src.is_file():
            dest = project_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if rel not in written:
                written.append(rel)

    agent._ensure_bank_portal_pages(project_dir, written)
    agent._ensure_bank_portal_fixtures(project_dir, written)
    return written
