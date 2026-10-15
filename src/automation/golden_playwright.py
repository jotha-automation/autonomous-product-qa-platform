"""Pre-built INC1353166 Playwright golden project — build once, materialize per pipeline run."""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.intake.qa_spec_chunker import chunk_qa_spec_text, obligations_from_chunks
from src.intake.test_naming import format_case_code

logger = logging.getLogger("golden_playwright")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO_ROOT / "src" / "agents" / "templates" / "playwright"
_AUTOMATION_ROOT = Path(__file__).resolve().parent
GOLDEN_ID = "inc1353166"
GOLDEN_DIR = _TEMPLATES / "golden" / GOLDEN_ID
GOLDEN_TEST_PLAN_PATH = GOLDEN_DIR / "golden-test-plan.json"
_BUNDLED_SPEC_FIXTURE = _AUTOMATION_ROOT / "fixtures" / "documents" / "inc1353166-testlink.txt"
_DEV_SPEC_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "documents" / "inc1353166-testlink.txt"
DEFAULT_SPEC_FIXTURE = _BUNDLED_SPEC_FIXTURE
DEFAULT_TARGET_URL = "https://bank-portal.up.railway.app"

SUPPORTED_NEGATIVE_VARIATIONS = frozenset(
    {
        "wrong_password",
        "unknown_user",
        "empty_token",
        "no_session",
        "state_skip",
        "malformed_input",
        "empty_required",
        "invalid_format",
        "amount_zero",
        "missing_enrollment",
        "invalid_state",
    }
)

_SKIP_AUTH_VARIATIONS = frozenset(
    {
        "no_session",
        "state_skip",
        "wrong_password",
        "unknown_user",
        "empty_token",
    }
)

_GOLDEN_ENV_EXAMPLE = _TEMPLATES / "golden" / GOLDEN_ID / ".env.example"


def _write_golden_env_files(project_dir: Path, target_url: str) -> None:
    """Ensure golden/local Playwright runs have demo secrets without pipeline materialization."""
    from src.automation.bank_portal import bank_portal_env_defaults, sync_run_secrets_to_project_env
    from src.services.project_env import merge_project_env

    project_dir = Path(project_dir)
    example_src = _GOLDEN_ENV_EXAMPLE if _GOLDEN_ENV_EXAMPLE.is_file() else None
    example_dest = project_dir / ".env.example"
    if example_src and example_src != example_dest:
        example_dest.write_text(example_src.read_text(encoding="utf-8"), encoding="utf-8")
    elif not example_dest.is_file():
        defaults = bank_portal_env_defaults()
        lines = [
            f"BASE_URL={target_url or DEFAULT_TARGET_URL}",
            "APTP_ALLOW_DEMO_SECRETS=true",
        ]
        for key, value in sorted(defaults.items()):
            lines.append(f"{key}={value}")
        example_dest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Runtime .env for local npx playwright test (gitignored)
    merge_project_env(project_dir / ".env", {"BASE_URL": target_url or DEFAULT_TARGET_URL})
    sync_run_secrets_to_project_env(
        project_dir,
        "golden-build",
        bank_portal=True,
        base_url=target_url,
    )


_COPY_IGNORE_NAMES = frozenset(
    {
        "node_modules",
        "reports",
        "evidence",
        "test-results",
        "playwright-report",
        ".env",
        "golden-test-plan.json",
    }
)


def use_golden_playwright() -> bool:
    """When true, bank-portal runs materialize a verified golden project instead of LLM codegen."""
    return os.getenv("APTP_USE_GOLDEN_PLAYWRIGHT", "true").strip().lower() in {"1", "true", "yes", "on"}


def use_golden_only() -> bool:
    """Demo mode: skip PW architect/generator/validation; single-pass golden execution (no healing)."""
    explicit = os.getenv("APTP_GOLDEN_ONLY", "").strip().lower()
    if explicit in {"0", "false", "no", "off"}:
        return False
    if explicit in {"1", "true", "yes", "on"}:
        return True
    # Default: when golden Playwright is enabled, treat automation as demo (no live AI codegen/healing).
    return use_golden_playwright()


def golden_project_available() -> bool:
    manifest = GOLDEN_DIR / "golden-manifest.json"
    tests = GOLDEN_DIR / "tests"
    return manifest.is_file() and tests.is_dir() and any(tests.glob("*.spec.ts"))


def inc1353166_spec_path(custom: Optional[str | Path] = None) -> Path:
    if custom:
        path = Path(custom)
        if path.is_file():
            return path
    env_path = os.getenv("APTP_INC1353166_SPEC_PATH", "").strip()
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    for candidate in (_BUNDLED_SPEC_FIXTURE, _DEV_SPEC_FIXTURE):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "INC1353166 TestLink spec not found. Expected bundled fixture at "
        f"{_BUNDLED_SPEC_FIXTURE} or dev fixture at {_DEV_SPEC_FIXTURE}."
    )


def load_inc1353166_obligations(*, spec_path: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """Parse TestLink PDF text into obligation dicts (32 EMPRESARIAL cases)."""
    path = inc1353166_spec_path(spec_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_qa_spec_text(text, source_filename=path.name)
    obligations = obligations_from_chunks(chunks)
    from src.automation.obligation_steps import is_business_day_obligation

    for obl in obligations:
        if is_business_day_obligation(obl):
            obl["automation_tier"] = "automated"
            obl["automation_blockers"] = []
            obl["automation_rationale"] = "Días hábiles simulables en portal demo (__aptpDemo)."
    return obligations


def build_inc1353166_test_plan(
    target_url: str,
    *,
    spec_path: Optional[str | Path] = None,
    include_negatives: bool = True,
) -> Dict[str, Any]:
    """Full test plan: 32 intake obligations + synthesized negative/boundary scenarios."""
    from src.automation.negative_scenarios import synthesize_negative_scenarios
    from src.automation.obligation_steps import map_obligation_to_scenario

    obligations = load_inc1353166_obligations(spec_path=spec_path)
    scenarios: List[Dict[str, Any]] = []
    for index, obl in enumerate(obligations, start=1):
        obl = dict(obl)
        obl["cp_code"] = format_case_code(index, obl.get("title") or obl.get("external_id") or "")
        scenario = map_obligation_to_scenario(obl, target_url, index=index)
        if obl.get("automation_tier") == "manual":
            scenario["playwright_skip"] = True
            scenario["manual"] = True
        scenarios.append(scenario)

    if include_negatives:
        from src.automation.obligation_steps import map_functional_scenario_to_scenario

        login_negatives = [
            {
                "id": "NEG-LOGIN-WRONG-PASSWORD",
                "title": "Login con clave incorrecta",
                "scenario_type": "negative",
                "input_variation": "wrong_password",
                "module": "login",
                "tags": ["login", "negative"],
            },
            {
                "id": "NEG-LOGIN-UNKNOWN-USER",
                "title": "Login con documento inexistente",
                "scenario_type": "negative",
                "input_variation": "unknown_user",
                "module": "login",
                "tags": ["login", "negative"],
            },
            {
                "id": "NEG-LOGIN-EMPTY-TOKEN",
                "title": "Login sin token de seguridad",
                "scenario_type": "negative",
                "input_variation": "empty_token",
                "module": "login",
                "tags": ["login", "negative"],
            },
        ]
        existing_ids = {s.get("id") for s in scenarios}
        for neg in login_negatives:
            if neg["id"] in existing_ids:
                continue
            neg_idx = len(scenarios) + 1
            neg_copy = dict(neg)
            neg_copy["cp_code"] = format_case_code(neg_idx, neg_copy["title"])
            mapped = map_functional_scenario_to_scenario(neg_copy, target_url, index=neg_idx)
            mapped["skip_auth"] = True
            mapped["input_variation"] = neg_copy.get("input_variation")
            scenarios.append(mapped)
            existing_ids.add(mapped.get("id"))

        explicit_negatives = [
            {
                "id": "NEG-SESSION-NO-AUTH",
                "title": "Acceso a ruta protegida sin sesión",
                "scenario_type": "security",
                "input_variation": "no_session",
                "module": "session",
                "tags": ["session", "security"],
            },
            {
                "id": "NEG-TRANSFER-ZERO-AMOUNT",
                "title": "Transferencia con monto cero",
                "scenario_type": "boundary",
                "input_variation": "amount_zero",
                "module": "transfer",
                "tags": ["transfer", "boundary"],
            },
            {
                "id": "NEG-TRANSFER-NO-ENROLLMENT",
                "title": "Transferencia ACH sin inscripción asociada",
                "scenario_type": "negative",
                "input_variation": "missing_enrollment",
                "module": "transfer",
                "tags": ["transfer", "negative"],
            },
            {
                "id": "NEG-PAYMENT-INVALID-STATE",
                "title": "Pago con inscripción no aceptada",
                "scenario_type": "negative",
                "input_variation": "invalid_state",
                "module": "payment",
                "tags": ["payment", "negative"],
            },
        ]
        for neg in explicit_negatives:
            if neg["id"] in existing_ids:
                continue
            neg_idx = len(scenarios) + 1
            neg_copy = dict(neg)
            neg_copy["cp_code"] = format_case_code(neg_idx, neg_copy["title"])
            mapped = map_functional_scenario_to_scenario(neg_copy, target_url, index=neg_idx)
            variation = str(neg_copy.get("input_variation") or "")
            mapped["input_variation"] = variation
            if variation in _SKIP_AUTH_VARIATIONS:
                mapped["skip_auth"] = True
            scenarios.append(mapped)
            existing_ids.add(mapped.get("id"))

        happy = [
            s
            for s in scenarios
            if not s.get("playwright_skip")
            and (s.get("scenario_type") or "happy_path") == "happy_path"
            and not str(s.get("id", "")).startswith("NEG-")
        ]
        neg_expanded = synthesize_negative_scenarios(happy, max_per_base=3)
        existing_ids = {s.get("id") for s in scenarios}
        for neg in neg_expanded:
            if not neg.get("parent_scenario_id") or neg.get("id") in existing_ids:
                continue
            variation = str(neg.get("input_variation") or "")
            if variation not in SUPPORTED_NEGATIVE_VARIATIONS:
                continue
            neg_idx = len(scenarios) + 1
            neg["cp_code"] = format_case_code(neg_idx, neg.get("title") or neg.get("id") or "")
            mapped = map_functional_scenario_to_scenario(neg, target_url, index=neg_idx)
            mapped["input_variation"] = variation
            if neg.get("scenario_type") != "happy_path":
                mapped["skip_auth"] = bool(mapped.get("skip_auth")) or variation in _SKIP_AUTH_VARIATIONS
            scenarios.append(mapped)
            existing_ids.add(mapped.get("id"))

    return {
        "aut_profile": "bank_portal",
        "target_url": target_url,
        "suite": {
            "name": "REQ INC1353166",
            "feature": "inscripciones",
            "aut_profile": "bank_portal",
            "browser_matrix": ["chromium"],
            "req_id": "INC1353166",
        },
        "scenarios": scenarios,
        "obligations_count": len(obligations),
    }


def build_golden_project(
    golden_dir: Optional[Path] = None,
    *,
    target_url: str = DEFAULT_TARGET_URL,
    spec_path: Optional[str | Path] = None,
    run_id: str = "golden-build",
) -> Tuple[List[str], Dict[str, Any]]:
    """Generate the verified golden Playwright tree under templates/golden/inc1353166."""
    from src.agents.playwright_generator import PlaywrightGeneratorAgent
    from src.automation.bank_portal import register_demo_secrets, register_no_token_demo_secrets

    dest = Path(golden_dir or GOLDEN_DIR)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    test_plan = build_inc1353166_test_plan(target_url, spec_path=spec_path)
    register_demo_secrets(run_id)
    register_no_token_demo_secrets(run_id)

    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    template_codegen: Dict[str, Any] = {
        "pages": {},
        "specs": {},
        "fixtures": {},
        "env": {},
        "template_only": True,
    }
    written = agent._generate_project(
        dest,
        test_plan,
        GOLDEN_ID,
        target_url,
        template_codegen,
        run_id=run_id,
        skip_bank_reconcile=True,
    )
    _write_golden_env_files(dest, target_url)

    manifest = {
        "golden_id": GOLDEN_ID,
        "req_id": "INC1353166",
        "target_url": target_url,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "obligations_count": test_plan.get("obligations_count", 0),
        "scenario_count": len(test_plan.get("scenarios") or []),
        "spec_files": sorted(p.name for p in (dest / "tests").glob("*.spec.ts")),
        "files_written": written,
    }
    (dest / "golden-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (dest / "golden-test-plan.json").write_text(
        json.dumps(test_plan, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        "Built golden Playwright project: %s scenarios, %s files -> %s",
        manifest["scenario_count"],
        len(written),
        dest,
    )
    return written, test_plan


def _copy_ignore(_dir: str, names: List[str]) -> set[str]:
    return {n for n in names if n in _COPY_IGNORE_NAMES}


def load_golden_test_plan(
    target_url: str,
    *,
    spec_path: Optional[str | Path] = None,
    golden_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load cached plan from golden template; rebuild only when cache is missing."""
    plan_path = (golden_dir or GOLDEN_DIR) / "golden-test-plan.json"
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if target_url:
            plan["target_url"] = target_url
        return plan
    return build_inc1353166_test_plan(target_url, spec_path=spec_path)


def materialize_golden_playwright(
    project_dir: Path,
    project_id: str,
    target_url: str,
    run_id: str = "",
    *,
    spec_path: Optional[str | Path] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Copy golden project into generated-test-projects/{project_id} and patch runtime env."""
    from src.automation.bank_portal import register_demo_secrets, register_no_token_demo_secrets, sync_run_secrets_to_project_env
    from src.services.project_env import merge_project_env

    if not golden_project_available():
        logger.info("Golden project missing — building into %s", GOLDEN_DIR)
        build_golden_project(target_url=target_url or DEFAULT_TARGET_URL, spec_path=spec_path, run_id=run_id or "golden-build")

    project_dir = Path(project_dir)
    if project_dir.exists():
        shutil.rmtree(project_dir)
    shutil.copytree(GOLDEN_DIR, project_dir, ignore=_copy_ignore)

    _write_golden_env_files(project_dir, target_url or DEFAULT_TARGET_URL)

    if run_id:
        register_demo_secrets(run_id)
        register_no_token_demo_secrets(run_id)

    merge_project_env(
        project_dir / ".env",
        {
            "BASE_URL": target_url or DEFAULT_TARGET_URL,
            **({"APTP_RUN_ID": run_id} if run_id else {}),
        },
    )
    sync_run_secrets_to_project_env(
        project_dir,
        run_id or "golden-materialize",
        bank_portal=True,
        base_url=target_url,
    )

    pkg_path = project_dir / "package.json"
    if pkg_path.is_file():
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        pkg["name"] = f"aptp-{project_id}"
        pkg_path.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")

    written = sorted(
        str(p.relative_to(project_dir))
        for p in project_dir.rglob("*")
        if p.is_file() and p.name not in _COPY_IGNORE_NAMES
    )
    test_plan = load_golden_test_plan(target_url or DEFAULT_TARGET_URL, spec_path=spec_path)
    return written, test_plan
