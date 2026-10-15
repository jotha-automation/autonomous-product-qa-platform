#!/usr/bin/env python3
"""Regenerate DEMO-QA Playwright project from fixed obligation step mapping."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.automation.obligation_steps import map_obligation_to_scenario
from src.services.target_url import resolve_target_url

TEMPLATES = _REPO / "src" / "agents" / "templates" / "playwright"


def _latest_pipeline_summary(project_dir: Path) -> dict | None:
    artifacts = project_dir / "aptp-artifacts"
    if not artifacts.is_dir():
        return None
    runs = sorted(artifacts.glob("run-*/pipeline_summary.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in runs:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def main() -> int:
    project_dir = _REPO / "generated-test-projects" / "DEMO-QA"
    project_dir.mkdir(parents=True, exist_ok=True)
    summary = _latest_pipeline_summary(project_dir) or {}
    results = summary.get("results") or {}
    obligations = (results.get("interpreted_intake") or {}).get("obligations") or []
    if not obligations:
        print("No obligations found in pipeline summary; aborting.")
        return 1

    target_url = resolve_target_url({"interpreted_intake": results.get("interpreted_intake") or {}}) or (
        "https://bank-portal.up.railway.app"
    )
    scenarios = [map_obligation_to_scenario(obl, target_url) for obl in obligations if isinstance(obl, dict)]
    test_plan = {
        "aut_profile": "bank_portal",
        "target_url": target_url,
        "suite": {"name": "DEMO-QA", "feature": "bank-portal", "browser_matrix": ["chromium"]},
        "scenarios": scenarios,
    }

    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    run_id = summary.get("run_id") or "demo-refresh"
    written = agent._generate_project(
        project_dir,
        test_plan,
        "DEMO-QA",
        target_url,
        {},
        run_id=run_id,
    )

    # Sync platform templates that must match backend automation contracts.
    for rel in (
        "playwright.config.ts",
        "pages/LoginPage.ts",
        "fixtures/evidence.ts",
        "fixtures/dynamic-auth.ts",
        "fixtures/video-evidence-reporter.ts",
    ):
        src = TEMPLATES / rel
        if src.is_file():
            dest = project_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if rel not in written:
                written.append(rel)

    tests_dir = project_dir / "tests"
    removed = 0
    if tests_dir.is_dir():
        for spec in tests_dir.glob("obl-emp-*.spec.ts"):
            spec.unlink()
            removed += 1

    print(f"Regenerated DEMO-QA: {len(scenarios)} scenarios, {len(written)} files, removed {removed} stale negative specs.")
    print(f"Target URL: {target_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
