#!/usr/bin/env python3
"""Build and optionally smoke-test the INC1353166 golden Playwright project."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src.automation.golden_playwright import (  # noqa: E402
    DEFAULT_TARGET_URL,
    GOLDEN_DIR,
    build_golden_project,
    golden_project_available,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build INC1353166 golden Playwright template")
    parser.add_argument("--target-url", default=DEFAULT_TARGET_URL, help="AUT base URL")
    parser.add_argument("--spec-path", default="", help="Override TestLink spec text path")
    parser.add_argument("--smoke", action="store_true", help="Run npx playwright test after build")
    parser.add_argument("--list-only", action="store_true", help="npx playwright test --list only")
    args = parser.parse_args()

    spec = args.spec_path or None
    written, test_plan = build_golden_project(
        target_url=args.target_url,
        spec_path=spec,
    )
    print(f"Golden project: {GOLDEN_DIR}")
    print(f"  obligations: {test_plan.get('obligations_count', 0)}")
    print(f"  scenarios:   {len(test_plan.get('scenarios') or [])}")
    print(f"  files:       {len(written)}")
    print(f"  available:   {golden_project_available()}")

    if args.smoke or args.list_only:
        subprocess.run(["npm", "install"], cwd=GOLDEN_DIR, check=True)
        cmd = ["npx", "playwright", "test", "--list"] if args.list_only else ["npx", "playwright", "test"]
        env = {**dict(**__import__("os").environ), "BASE_URL": args.target_url}
        result = subprocess.run(cmd, cwd=GOLDEN_DIR, env=env)
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
