"""Playwright code generator — live exploration, templates + LLM enrichment, validate & execute."""
import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def _phase_timeout_s(name: str, default: int) -> int:
    """Per-phase subprocess time budget (seconds) from env, with a safe floor."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# Per-phase time budgets for _validate_and_execute subprocess steps.
_PHASE_TIMEOUT_ENV = {
    "npm_install": ("APTP_NPM_INSTALL_TIMEOUT_S", 300),
    "playwright_install": ("APTP_PLAYWRIGHT_INSTALL_TIMEOUT_S", 300),
    "playwright_list": ("APTP_PLAYWRIGHT_LIST_TIMEOUT_S", 120),
    "playwright_test": ("APTP_PLAYWRIGHT_TEST_TIMEOUT_S", 1800),
}

from src.agents.base import BaseAgent
from src.agents.guardrails.agent_guardrails import reject_unsafe_generated_code
from src.agents.token_policy import output_tokens, prompt_budget
from src.playwright_version import PLAYWRIGHT_NPM_SPEC
from src.runtime_policy import skip_playwright_install, use_mock_playwright_execution
from src.services.llm_routing import codegen_deployment

logger = logging.getLogger("playwright_generator")

TEMPLATES_DIR = Path(__file__).parent / "templates" / "playwright"

PROTECTED_FIXTURES = frozenset({"dynamic-auth.ts", "evidence.ts", "video-evidence-reporter.ts"})

_INVALID_PAGE_PATTERNS = (
    re.compile(r"async\s+constructor\b"),
    re.compile(r"\)\(\)\s*\{"),
    re.compile(r"constructor\s*\([^)]*\)\s*\(\)\s*\{"),
    re.compile(r"async\s+\w+\s*\([^)]*\)\s*\{[^}]*async\s+\w+\s*\("),
)

_NESTED_METHOD_BODY = re.compile(
    r"^async\s+(?P<name>\w+)\s*\([^)]*\)\s*\{\s*(?P<body>.*)\s*\}$",
    re.DOTALL,
)

# Known fixture/page modules → named exports (never default everything to captureStepEvidence).
_FIXTURE_MODULE_EXPORTS: Dict[str, List[str]] = {
    "evidence": ["captureStepEvidence"],
    "test-data": ["validManualData", "invalidManualData", "validArchivoPlano"],
}

# bank-portal-auth is skipped — bank portal specs use LoginPage.login() in beforeEach.
_SKIPPED_IMPORT_MODULES = frozenset({"bank-portal-auth"})

_BANK_PORTAL_PAGE_TEMPLATES = frozenset({"LoginPage", "InscripcionesPage", "ConsultasPage"})

_LOCAL_IMPORT_RE = re.compile(
    r"import\s+(?:\{([^}]*)\}|\*\s+as\s+\w+)\s+from\s+['\"](\.\./(?:pages|fixtures)/[^'\"]+)['\"]",
)

_IMPORT_FROM_RE = re.compile(
    r"import\s+(?P<kind>\*|(?:\{[^}]*\}))\s+from\s+['\"](?P<module>[^'\"]+)['\"]\s*;?",
)
_IMPORT_SYMBOLS_RE = re.compile(r"\{([^}]*)\}")


class PlaywrightGeneratorAgent(BaseAgent):
    """Playwright code generator — live exploration, templates + LLM enrichment, validate & execute.

    Memory contract:
      Reads: test_plan, generated_code
      Writes: generated_code, exploration (also execution_key: generated_code when run_id present)
    """

    @staticmethod
    def _browsers_available() -> bool:
        """Detect pre-installed Playwright browsers (e.g., Docker image) to skip per-run install."""
        from src.services.playwright_runtime import browsers_available

        return browsers_available()

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        test_plan = context.get("test_plan") or self.read_memory("test_plan", default={})
        project_id = context.get("project_id", "demo")
        run_id = context.get("execution_id") or context.get("run_id") or ""
        from src.services.target_url import resolve_target_url

        base_url = resolve_target_url(context)
        root = Path(os.getenv("PLAYWRIGHT_PROJECTS_ROOT", "generated-test-projects"))
        project_dir = root / project_id

        exploration: Dict[str, Any] = {}
        if base_url and not use_mock_playwright_execution():
            from src.services.playwright_explorer import explore_test_plan

            try:
                exploration = await explore_test_plan(test_plan, base_url, run_id=run_id or None)
                if exploration.get("scenarios"):
                    test_plan = {**test_plan, "scenarios": exploration["scenarios"]}
            except Exception as exc:
                logger.warning("Live Playwright exploration failed: %s", exc)
                exploration = {"skipped": True, "reason": str(exc)}

        codegen: Dict[str, Any] = {}
        bank_portal = (
            test_plan.get("aut_profile") == "bank_portal"
            or (test_plan.get("suite") or {}).get("aut_profile") == "bank_portal"
        )
        template_codegen: Dict[str, Any] = {
            "pages": {},
            "specs": {},
            "fixtures": {},
            "env": {},
            "template_only": True,
        }

        if bank_portal:
            from src.automation.golden_playwright import GOLDEN_ID, materialize_golden_playwright, use_golden_playwright

            if use_golden_playwright():
                files_written, golden_plan = await asyncio.to_thread(
                    materialize_golden_playwright,
                    project_dir,
                    project_id,
                    base_url or "",
                    run_id,
                )
                test_plan = {
                    **test_plan,
                    **{k: golden_plan[k] for k in ("scenarios", "suite", "aut_profile") if k in golden_plan},
                }
                codegen = {**template_codegen, "golden_template": GOLDEN_ID, "template_only": True}
            else:
                files_written = await asyncio.to_thread(
                    self._generate_project,
                    project_dir, test_plan, project_id, base_url, template_codegen, run_id, exploration
                )
                codegen = template_codegen
            if not use_mock_playwright_execution():
                list_result = await self._playwright_list_only(
                    project_dir, base_url, run_id, bank_portal=bank_portal
                )
                if not list_result.get("ok"):
                    logger.warning(
                        "Bank portal template compile check failed: %s — specs unchanged",
                        list_result.get("stderr", "")[:500],
                    )
        else:
            codegen = await self._llm_codegen_hints(test_plan, base_url, context, exploration)
            files_written = await asyncio.to_thread(
                self._generate_project,
                project_dir, test_plan, project_id, base_url, codegen, run_id, exploration
            )
        self._write_exploration_inventory(project_dir, exploration, files_written)

        generation_execution: Dict[str, Any] = {"skipped": True}
        if not use_mock_playwright_execution() and os.getenv("APTP_SKIP_GENERATOR_EXECUTE", "false").lower() not in {
            "1",
            "true",
            "yes",
        }:
            generation_execution = await self._validate_and_execute(
                project_dir, base_url, run_id, bank_portal=bank_portal
            )

        result = {
            "project_path": str(project_dir),
            "files": files_written,
            "suite": test_plan.get("suite", {}),
            "codegen": codegen,
            "exploration": exploration,
            "generation_execution": generation_execution,
            "template_only": bool(codegen.get("template_only")),
            "generated_tests": [
                {
                    "test_id": s.get("id"),
                    "file_path": f"tests/{s.get('name', 'test')}.spec.ts",
                    "page_object": s.get("page_object"),
                }
                for s in test_plan.get("scenarios", [])
            ],
        }

        self.store_short_term_memory("generated_code", result)
        exec_id = context.get("execution_id") or context.get("run_id")
        if exec_id:
            self.memory.write(self.memory.execution_key(exec_id, "generated_code"), result)
        return result

    @staticmethod
    def _merge_codegen(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
        """Merge LLM output with exploration fallbacks (specs/steps only)."""
        if not primary:
            return fallback
        merged = {**fallback, **primary}
        merged["pages"] = {**(fallback.get("pages") or {}), **(primary.get("pages") or {})}
        merged["specs"] = {**(fallback.get("specs") or {}), **(primary.get("specs") or {})}
        merged["fixtures"] = {**(fallback.get("fixtures") or {}), **(primary.get("fixtures") or {})}
        merged["env"] = {**(fallback.get("env") or {}), **(primary.get("env") or {})}
        if primary.get("from_exploration"):
            merged["template_only"] = False
        return merged

    @staticmethod
    def _collect_codegen_context(context: Dict[str, Any], exploration: Dict[str, Any]) -> Dict[str, Any]:
        interpreted = context.get("interpreted_intake") or {}
        assets = context.get("test_assets") or context.get("assets") or []
        obligations = interpreted.get("obligations") or []
        env_hints: Dict[str, str] = {}
        run_id = context.get("run_id") or context.get("execution_id") or ""
        if run_id:
            from src.services.run_secret_store import RunSecretStore

            store = RunSecretStore.for_run(run_id)
            for key in store.list_keys():
                env_hints[f"APTP_SECRET_{key.replace('.', '_').upper()}"] = "[from intake — use RunSecretStore value]"
        return {
            "obligations": obligations,
            "obligation_count": len(obligations),
            "assets": [
                {
                    "key": a.get("key"),
                    "type": a.get("asset_type"),
                    "usage": a.get("usage_hint"),
                    "fields": list((a.get("fields") or {}).keys()),
                }
                for a in (assets if isinstance(assets, list) else [])[:8]
            ],
            "environment": interpreted.get("environment") or {},
            "explorer_inventory": exploration.get("inventory") or {},
            "discovered_testids": exploration.get("discovered_testids") or {},
            "env_key_names": list(env_hints.keys()),
        }

    @staticmethod
    def _origin_base_url(url: str | None) -> str:
        if not url:
            return "https://example.com"
        parsed = urlparse(url.strip())
        if not parsed.scheme:
            parsed = urlparse(f"https://{url.strip()}")
        return f"{parsed.scheme}://{parsed.netloc}" or "https://example.com"

    async def _validate_and_execute(
        self, project_dir: Path, base_url: str | None, run_id: str, *, bank_portal: bool = False
    ) -> Dict[str, Any]:
        self._scaffold_missing_local_imports(project_dir, [], bank_portal=bank_portal)
        env = {**os.environ, "CI": "true"}
        browsers_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
        if browsers_path:
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
        if base_url:
            env["BASE_URL"] = self._origin_base_url(base_url)
        if run_id:
            from src.services.run_secret_store import RunSecretStore

            store = RunSecretStore.for_run(run_id)
            for key in store.list_keys():
                env[f"APTP_SECRET_{key.replace('.', '_').upper()}"] = store.get(key)

        async def _run(cmd: List[str], timeout_s: int) -> tuple[int, str, str]:
            if run_id:
                from src.services.run_cancellation import ensure_not_cancelled
                ensure_not_cancelled(run_id)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError, Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
                # Exit 124 mirrors GNU `timeout`; "timed out after" is flagged as infra downstream.
                return 124, "", f"{' '.join(cmd)} timed out after {timeout_s}s"
            return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

        steps_log: List[Dict[str, Any]] = []
        install_skipped = skip_playwright_install() or self._browsers_available()
        for label, cmd in (
            ("npm_install", ["npm", "install", "--no-audit", "--no-fund"]),
            ("playwright_install", ["npx", "playwright", "install", "chromium"]),
            ("playwright_list", ["npx", "playwright", "test", "--list"]),
            ("playwright_test", ["npx", "playwright", "test"]),
        ):
            if label == "playwright_install" and install_skipped:
                steps_log.append({"step": label, "exit_code": 0, "skipped": True})
                continue
            timeout_s = _phase_timeout_s(*_PHASE_TIMEOUT_ENV[label])
            code, stdout, stderr = await _run(cmd, timeout_s)
            steps_log.append({"step": label, "exit_code": code, "stderr_tail": stderr[-800:]})
            if code != 0 and label == "playwright_list" and "Cannot find module" in stderr:
                healed = self._scaffold_missing_local_imports(project_dir, [], bank_portal=bank_portal)
                if healed:
                    code, stdout, stderr = await _run(cmd, timeout_s)
                    steps_log.append(
                        {"step": f"{label}_healed", "exit_code": code, "stderr_tail": stderr[-800:]}
                    )
            if code != 0 and label == "playwright_list":
                repair_result, repairs = await self._autonomous_compile_repair(
                    project_dir,
                    base_url,
                    run_id,
                    bank_portal=bank_portal,
                    initial_stderr=stderr,
                )
                if repairs:
                    steps_log.append(
                        {
                            "step": "autonomous_repair",
                            "repairs": repairs,
                            "ok": repair_result.get("ok", False),
                            "stderr_tail": (repair_result.get("stderr") or "")[-800:],
                        }
                    )
                if repair_result.get("ok"):
                    code, stdout, stderr = 0, repair_result.get("stdout", ""), ""
                    steps_log.append({"step": "playwright_list_repaired", "exit_code": 0})
            if code != 0 and label in {"npm_install", "playwright_list"}:
                return {
                    "skipped": False,
                    "status": "validation_failed",
                    "steps": steps_log,
                    "passed": 0,
                    "failed": 1,
                }

        passed = failed = 0
        report_path = project_dir / "reports" / "results.json"
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                passed = int(report.get("stats", {}).get("expected", 0) or 0)
                failed = int(report.get("stats", {}).get("unexpected", 0) or 0)
            except (json.JSONDecodeError, OSError):
                pass

        return {
            "skipped": False,
            "status": "completed" if failed == 0 and steps_log[-1]["exit_code"] == 0 else "failed",
            "steps": steps_log,
            "passed": passed,
            "failed": failed,
        }

    async def _ensure_npm_dependencies(self, project_dir: Path) -> tuple[bool, str]:
        """Install npm deps when package.json exists but node_modules is missing."""
        package_json = project_dir / "package.json"
        if not package_json.is_file():
            return True, ""
        playwright_pkg = project_dir / "node_modules" / "@playwright" / "test"
        if playwright_pkg.is_dir():
            return True, ""
        timeout_s = _phase_timeout_s(*_PHASE_TIMEOUT_ENV["npm_install"])
        proc = await asyncio.create_subprocess_exec(
            "npm",
            "install",
            "--no-audit",
            "--no-fund",
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            return False, f"npm install timed out after {timeout_s}s"
        stderr_text = stderr.decode(errors="replace")
        if (proc.returncode or 0) != 0:
            return False, stderr_text[-2000:]
        return True, ""

    async def _playwright_list_only(
        self, project_dir: Path, base_url: str | None, run_id: str, *, bank_portal: bool = False
    ) -> Dict[str, Any]:
        """Run playwright test --list to verify specs compile."""
        self._scaffold_missing_local_imports(project_dir, [], bank_portal=bank_portal)
        deps_ok, deps_err = await self._ensure_npm_dependencies(project_dir)
        if not deps_ok:
            return {
                "ok": False,
                "exit_code": 1,
                "stderr": deps_err,
                "stdout": "",
            }
        env = {**os.environ, "CI": "true"}
        browsers_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
        if browsers_path:
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
        if base_url:
            env["BASE_URL"] = self._origin_base_url(base_url)
        if run_id:
            from src.services.run_secret_store import RunSecretStore

            store = RunSecretStore.for_run(run_id)
            for key in store.list_keys():
                env[f"APTP_SECRET_{key.replace('.', '_').upper()}"] = store.get(key)

        proc = await asyncio.create_subprocess_exec(
            "npx",
            "playwright",
            "test",
            "--list",
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        code = proc.returncode or 0
        stderr_text = stderr.decode(errors="replace")
        if code != 0 and "Cannot find module" in stderr_text:
            if self._scaffold_missing_local_imports(project_dir, [], bank_portal=bank_portal):
                proc = await asyncio.create_subprocess_exec(
                    "npx",
                    "playwright",
                    "test",
                    "--list",
                    cwd=str(project_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, stderr = await proc.communicate()
                code = proc.returncode or 0
                stderr_text = stderr.decode(errors="replace")
            elif "@playwright/test" in stderr_text or "playwright.config" in stderr_text:
                deps_ok, deps_err = await self._ensure_npm_dependencies(project_dir)
                if deps_ok:
                    proc = await asyncio.create_subprocess_exec(
                        "npx",
                        "playwright",
                        "test",
                        "--list",
                        cwd=str(project_dir),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                    )
                    stdout, stderr = await proc.communicate()
                    code = proc.returncode or 0
                    stderr_text = stderr.decode(errors="replace")
                else:
                    stderr_text = deps_err or stderr_text
        return {
            "ok": code == 0,
            "exit_code": code,
            "stderr": stderr_text[-2000:],
            "stdout": stdout.decode(errors="replace")[-2000:],
        }

    async def validate_generated_project(
        self,
        project_dir: Path,
        base_url: str | None,
        run_id: str,
        *,
        bank_portal: bool = False,
    ) -> Dict[str, Any]:
        """Run `npx playwright test --list` and return a validation result."""
        list_result = await self._playwright_list_only(project_dir, base_url, run_id, bank_portal=bank_portal)
        return {
            "status": "completed" if list_result.get("ok") else "validation_failed",
            "validation": list_result,
            "passed": 1 if list_result.get("ok") else 0,
            "failed": 0 if list_result.get("ok") else 1,
        }

    def _failing_ts_files(self, stderr: str, project_dir: Path) -> List[Path]:
        """Identify generated .ts files referenced by a compile/syntax error.

        Robust against paths containing spaces by matching project files against
        their absolute path or basename appearing in the compiler output.
        """
        if not stderr:
            return []
        candidates: List[Path] = []
        for sub in ("tests", "pages", "fixtures"):
            d = project_dir / sub
            if d.is_dir():
                candidates.extend(sorted(d.glob("*.ts")))
        failing: List[Path] = []
        for path in candidates:
            if path.name in PROTECTED_FIXTURES:
                continue
            if str(path) in stderr or path.name in stderr:
                failing.append(path)
        return failing

    def _deterministic_ts_fixes(self, files: List[Path]) -> List[Dict[str, Any]]:
        """Fix well-known syntax errors without an LLM (e.g. malformed toHaveURL regex)."""
        changes: List[Dict[str, Any]] = []
        for path in files:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fixed = self._repair_invalid_url_regex(content)
            if fixed != content:
                path.write_text(fixed, encoding="utf-8")
                changes.append(
                    {
                        "file": f"{path.parent.name}/{path.name}",
                        "strategy": "deterministic_regex_fix",
                    }
                )
        return changes

    @staticmethod
    def _repair_invalid_url_regex(content: str) -> str:
        """Rewrite toHaveURL(/.../) arguments into valid JS regex literals."""

        def _repl(match: "re.Match[str]") -> str:
            arg = match.group(1).strip()
            if not (arg.startswith("/") and arg.endswith("/") and len(arg) >= 2):
                return match.group(0)
            inner = arg[1:-1]
            # Only repair when an unescaped '/' makes the literal malformed; leave
            # already-valid regex literals (e.g. /.+/) untouched.
            if not re.search(r"(?<!\\)/", inner):
                return match.group(0)
            escaped = inner.replace("\\/", "/").replace("/", "\\/")
            return f"toHaveURL(/{escaped}/)"

        return re.sub(r"toHaveURL\(([^;]*?)\)", _repl, content)

    async def _llm_repair_ts_files(
        self, files: List[Path], stderr: str, run_id: str
    ) -> List[Dict[str, Any]]:
        """Autonomously rewrite failing spec/page files via the codegen model."""
        from src.agents.token_policy import truncate_text

        changes: List[Dict[str, Any]] = []
        for path in files[:6]:
            if run_id:
                from src.services.run_cancellation import ensure_not_cancelled
                ensure_not_cancelled(run_id)
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = f"{path.parent.name}/{path.name}"
            task = (
                "TASK: Fix the TypeScript/Playwright COMPILE error in the file below so "
                "`npx playwright test --list` succeeds.\n"
                "Return JSON: {\"file_content\": \"<entire corrected file content>\"}\n"
                "Rules:\n"
                "- Fix only syntax/compile/import errors. Preserve the test intent, structure, "
                "imports and Playwright APIs already used.\n"
                "- Keep relative imports (../pages, ../fixtures) intact.\n"
                "- Output the COMPLETE file, no markdown fences, no commentary.\n\n"
                f"FILE: {rel}\n"
                f"COMPILER ERROR OUTPUT:\n{truncate_text(stderr, agent_name=self.agent_name)}\n\n"
                f"CURRENT FILE CONTENT:\n{content}\n"
            )
            try:
                parsed = await self.ask_json(
                    task,
                    {"file_content": content},
                    temperature=0.0,
                    max_tokens=output_tokens(self.agent_name, chars_hint=len(content) * 2),
                    use_fallback_on_failure=True,
                )
            except Exception as exc:  # noqa: BLE001 — repair must never crash the pipeline
                logger.warning("LLM repair failed for %s: %s", rel, exc)
                continue
            new_content = (parsed or {}).get("file_content") if isinstance(parsed, dict) else None
            if isinstance(new_content, str):
                new_content = new_content.strip()
                if new_content and new_content != content.strip() and "test(" in new_content + content:
                    path.write_text(new_content + ("\n" if not new_content.endswith("\n") else ""), encoding="utf-8")
                    changes.append({"file": rel, "strategy": "llm_repair"})
        return changes

    async def _autonomous_compile_repair(
        self,
        project_dir: Path,
        base_url: str | None,
        run_id: str,
        *,
        bank_portal: bool = False,
        initial_stderr: str = "",
        max_rounds: int = 3,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Autonomously repair compile/syntax errors until `playwright test --list` passes.

        Deterministic fixes run first (cheap, no tokens); the codegen model is used
        only for errors that deterministic rules cannot resolve. Bounded by max_rounds.
        """
        repairs: List[Dict[str, Any]] = []
        current_stderr = initial_stderr
        last_result: Dict[str, Any] = {"ok": False, "stderr": initial_stderr}
        for round_index in range(max_rounds):
            if run_id:
                from src.services.run_cancellation import ensure_not_cancelled
                ensure_not_cancelled(run_id)
            failing = self._failing_ts_files(current_stderr, project_dir)
            if not failing:
                break
            changed = self._deterministic_ts_fixes(failing)
            if not changed:
                changed = await self._llm_repair_ts_files(failing, current_stderr, run_id)
            if not changed:
                logger.warning(
                    "Autonomous repair round %d produced no changes; giving up", round_index + 1
                )
                break
            repairs.extend(changed)
            logger.info(
                "Autonomous repair round %d applied %d change(s); re-validating",
                round_index + 1,
                len(changed),
            )
            last_result = await self._playwright_list_only(
                project_dir, base_url, run_id, bank_portal=bank_portal
            )
            if last_result.get("ok"):
                break
            current_stderr = last_result.get("stderr", "")
        return last_result, repairs

    async def regenerate_template_specs(
        self,
        context: Dict[str, Any],
        test_plan: Dict[str, Any],
        project_dir: Path,
    ) -> Dict[str, Any]:
        """Regenerate specs from obligation templates (empty LLM hints) and validate compile."""
        project_id = context.get("project_id", "demo")
        run_id = context.get("execution_id") or context.get("run_id") or ""
        from src.services.target_url import resolve_target_url

        base_url = resolve_target_url(context)
        exploration: Dict[str, Any] = context.get("exploration") or {}
        template_codegen: Dict[str, Any] = {
            "pages": {},
            "specs": {},
            "fixtures": {},
            "env": {},
            "template_only": True,
        }
        files_written = await asyncio.to_thread(
            self._generate_project,
            project_dir,
            test_plan,
            project_id,
            base_url,
            template_codegen,
            run_id,
            exploration,
        )
        bank_portal = (
            test_plan.get("aut_profile") == "bank_portal"
            or (test_plan.get("suite") or {}).get("aut_profile") == "bank_portal"
        )
        list_result = await self._playwright_list_only(
            project_dir, base_url, run_id, bank_portal=bank_portal
        )
        generation_execution = {
            "skipped": False,
            "status": "completed" if list_result.get("ok") else "validation_failed",
            "steps": [{"step": "playwright_list", **list_result}],
            "passed": 0,
            "failed": 0 if list_result.get("ok") else 1,
        }
        return {
            "files": files_written,
            "generation_execution": generation_execution,
            "template_only": True,
            "ok": list_result.get("ok", False),
        }

    @staticmethod
    def _slim_scenarios_for_codegen(scenarios: List[Dict]) -> List[Dict]:
        slim: List[Dict] = []
        for sc in scenarios:
            slim.append(
                {
                    "id": sc.get("id"),
                    "name": sc.get("name"),
                    "page_object": sc.get("page_object"),
                    "tags": sc.get("tags") or [],
                    "locators": sc.get("locators"),
                    "evidence_steps": sc.get("evidence_steps") or [],
                    "steps": sc.get("steps") or [],
                    "assertions": sc.get("assertions") or [],
                    "manual": sc.get("manual") or sc.get("automation_tier") == "manual",
                }
            )
        return slim

    async def _request_codegen_json(self, task_prompt: str, *, scenario_count: int = 1) -> Dict[str, Any]:
        """Up to 3 LLM attempts on the codegen deployment (Kimi when hybrid is enabled)."""
        strict_suffix = (
            "\n\nRespond with a single valid JSON object only. No markdown fences or commentary. "
            "Escape double quotes inside strings. Keep method bodies short."
        )
        codegen_dep = codegen_deployment()
        last_raw = ""
        for attempt in range(3):
            prompt = task_prompt if attempt == 0 else task_prompt + strict_suffix
            try:
                raw = await self.ask_llm(
                    prompt,
                    max_tokens=output_tokens(self.agent_name, item_count=scenario_count),
                    temperature=0.1,
                    expect_json=True,
                    deployment_override=codegen_dep,
                )
            except Exception as exc:
                from src.services.llm_routing import is_deployment_fallback_error

                if is_deployment_fallback_error(exc):
                    logger.warning(
                        "Playwright codegen unavailable on all deployments; using deterministic templates: %s",
                        exc,
                    )
                    return {
                        "pages": {},
                        "specs": {},
                        "fixtures": {},
                        "env": {},
                        "template_only": True,
                        "llm_unavailable": "rate_limited",
                    }
                raise
            last_raw = raw or ""
            parsed = self.parse_json_response(raw, None)
            if isinstance(parsed, dict) and isinstance(parsed.get("specs"), dict):
                parsed.setdefault("pages", {})
                parsed.setdefault("fixtures", {})
                parsed.setdefault("env", {})
                return parsed
        logger.warning(
            "Playwright codegen hints unavailable after 3 attempts; using templates. "
            "Response prefix: %s",
            last_raw[:500],
        )
        return {"pages": {}, "specs": {}, "fixtures": {}, "env": {}, "template_only": True}

    @staticmethod
    def _codegen_batch_size(scenario_count: int = 0) -> int:
        """Dynamic batch sizing based on scenario count and complexity.

        Small batches (1-4) for complex scenarios to reduce LLM token waste.
        Larger batches (8-16) for simple/high-volume scenarios to reduce API calls.
        """
        raw = (os.getenv("APTP_PLAYWRIGHT_CODEGEN_BATCH_SIZE") or "auto").strip()
        if raw.lower() == "auto":
            # Auto-sizing: smaller batches for few scenarios, larger for many
            if scenario_count <= 4:
                return 4  # Small batches for precision
            elif scenario_count <= 16:
                return 8  # Medium batches for balance
            elif scenario_count <= 32:
                return 12  # Larger batches for volume
            else:
                return 16  # Maximum batch for high volume
        try:
            # Allow up to 24 for expert users who know their LLM can handle it
            return max(1, min(24, int(raw)))
        except ValueError:
            return 8

    async def _llm_codegen_hints(
        self,
        test_plan: Dict,
        base_url: Optional[str],
        context: Dict,
        exploration: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        scenarios = test_plan.get("scenarios", [])
        if not scenarios:
            return {"pages": {}, "specs": {}, "fixtures": {}, "env": {}}

        intake_ctx = self._collect_codegen_context(context, exploration or {})
        batch_size = self._codegen_batch_size(len(scenarios))
        merged: Dict[str, Any] = {"pages": {}, "specs": {}, "fixtures": {}, "env": {}}

        # OPTIMIZATION: Process batches with controlled concurrency to reduce wall-clock time
        # while respecting LLM API rate limits. Uses semaphore to limit concurrent calls.
        max_concurrent = int(os.getenv("APTP_CODEGEN_MAX_CONCURRENT", "2"))
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_batch(start_idx: int) -> Optional[Dict[str, Any]]:
            async with semaphore:
                run_id = (context or {}).get("run_id") or (context or {}).get("execution_id")
                if run_id:
                    from src.services.run_cancellation import ensure_not_cancelled
                    ensure_not_cancelled(run_id)
                if merged.get("llm_unavailable"):
                    return None
                batch = scenarios[start_idx : start_idx + batch_size]
                plan_slice = {
                    "target_url": base_url or test_plan.get("target_url"),
                    "suite": test_plan.get("suite", {}),
                    "scenarios": self._slim_scenarios_for_codegen(batch),
                    "batch": f"{start_idx // batch_size + 1}/{(len(scenarios) + batch_size - 1) // batch_size}",
                }
                # OPTIMIZED PROMPT: Compact formatting reduces tokens by ~20-25%
                task = self._build_compact_codegen_prompt(base_url, intake_ctx, plan_slice, len(batch), len(scenarios))
                return await self._request_codegen_json(task, scenario_count=len(batch))

        # Process all batches with controlled concurrency
        batch_tasks = [process_batch(start) for start in range(0, len(scenarios), batch_size)]
        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

        # Merge results, handling any failures gracefully
        for idx, result in enumerate(batch_results):
            if isinstance(result, Exception):
                logger.error("Codegen batch %d failed: %s", idx + 1, result)
                continue
            if result is None:
                continue
            if result.get("llm_unavailable"):
                merged["llm_unavailable"] = True
                break
            merged = self._merge_codegen(merged, result)

        fallback = (exploration or {}).get("codegen") or {}
        return self._merge_codegen(merged, fallback)

    def _build_compact_codegen_prompt(
        self,
        base_url: Optional[str],
        intake_ctx: Dict[str, Any],
        plan_slice: Dict[str, Any],
        batch_size: int,
        total_scenarios: int,
    ) -> str:
        """Build token-optimized codegen prompt with compact formatting.

        OPTIMIZATION: Minimizes whitespace and uses abbreviated keys to reduce
        token count by ~20-25% compared to verbose prompts, lowering LLM costs
        and improving response times without sacrificing clarity.
        """
        return (
            f"GEN Playwright TS specs for {batch_size} scenarios. Platform provides package.json,playwright.config.ts,fixtures.\n"
            f"Base:{base_url or '$BASE_URL'}\n"
            f'JSON:{{"env":{{"BASE_URL":s}},"pages":{{"<C>":{{"src":s,"imp":[s],"mth":[{{"n":s,"b":s}}]}}}},'
            f'"fixtures":{{"<p>":s}},"specs":{{"<id>":{{"f":s,"ei":[s],"tbl":[s],"t":n,"bel":[s]}}}}}}\n'
            f"RULES:\n-ALL scenarios in TEST PLAN\n-manual→['test.skip(true,\\'M\\');']\n-Use INTAKE testIds\n-No nested async\n"
            f"INTAKE:{self.compact_json(intake_ctx, max_chars=prompt_budget(self.agent_name, total_scenarios * 250))}\n"
            f"PLAN:{self.compact_json(plan_slice)}"
        )

    @staticmethod
    def _write_exploration_inventory(
        project_dir: Path, exploration: Dict[str, Any], files_written: List[str]
    ) -> None:
        payload: Dict[str, Any] = {
            "files": files_written,
            "project_path": str(project_dir),
        }
        if exploration.get("inventory"):
            payload.update(exploration["inventory"])
        if exploration.get("discovered_testids"):
            payload["discovered_testids"] = exploration["discovered_testids"]
        if len(payload) > 2 or exploration.get("inventory"):
            (project_dir / "generated-inventory.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    @staticmethod
    def _read_page_template(page_class: str) -> str:
        path = TEMPLATES_DIR / "pages" / f"{page_class}.ts"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return PlaywrightGeneratorAgent._minimal_page_stub(page_class)

    @staticmethod
    def _minimal_page_stub(page_class: str) -> str:
        return f"""import {{ Page }} from '@playwright/test';

export class {page_class} {{
  constructor(protected page: Page) {{}}
}}
"""

    @staticmethod
    def _minimal_fixture_stub(rel_path: str) -> str:
        basename = Path(rel_path).stem.replace("-", "_")
        return f"export const {basename} = {{}};\n"

    @classmethod
    def _collect_local_imports(cls, project_dir: Path) -> Dict[str, List[str]]:
        imports: Dict[str, set[str]] = {}
        tests_dir = project_dir / "tests"
        if not tests_dir.is_dir():
            return {}
        for spec in tests_dir.rglob("*.spec.ts"):
            content = spec.read_text(encoding="utf-8", errors="ignore")
            for match in _LOCAL_IMPORT_RE.finditer(content):
                symbols_str, module = match.group(1), match.group(2)
                module = module.replace("\\", "/")
                syms = [s.strip() for s in (symbols_str or "").split(",") if s.strip()]
                imports.setdefault(module, set()).update(syms)
        return {k: sorted(v) for k, v in imports.items()}

    def _ensure_bank_portal_pages(self, project_dir: Path, written: List[str]) -> None:
        for page_class in sorted(_BANK_PORTAL_PAGE_TEMPLATES):
            rel = f"pages/{page_class}.ts"
            dest = project_dir / "pages" / f"{page_class}.ts"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(self._read_page_template(page_class), encoding="utf-8")
            if rel not in written:
                written.append(rel)

    def _ensure_bank_portal_fixtures(self, project_dir: Path, written: List[str]) -> None:
        from src.automation.bank_portal import (
            MALFORMED_FILE_FIXTURE,
            PLAIN_FILE_FIXTURE,
            PLAIN_FILE_SAMPLE,
        )

        aut_fixtures = Path(__file__).resolve().parents[1] / "automation" / "fixtures"
        for fixture_ref, default_text in (
            (PLAIN_FILE_FIXTURE, PLAIN_FILE_SAMPLE + "\n"),
            (MALFORMED_FILE_FIXTURE, None),
        ):
            rel_path = fixture_ref.removeprefix("fixtures/")
            dest = project_dir / "fixtures" / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if default_text is not None:
                if not dest.is_file():
                    dest.write_text(default_text, encoding="utf-8")
            else:
                src = aut_fixtures / "archivos" / Path(rel_path).name
                if src.is_file():
                    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            rel = f"fixtures/{rel_path}"
            if rel not in written:
                written.append(rel)

    def _scaffold_missing_local_imports(
        self,
        project_dir: Path,
        written: List[str],
        *,
        bank_portal: bool = False,
    ) -> bool:
        """Ensure every locally-imported page/fixture module exists. Returns True if any file was created."""
        created = False
        imports = self._collect_local_imports(project_dir)
        if bank_portal:
            imports.setdefault("../pages/LoginPage", ["LoginPage"])

        for module_path, symbols in imports.items():
            norm = module_path.replace("\\", "/")
            if norm.startswith("../fixtures/"):
                rel = norm[len("../fixtures/") :]
                if not rel.endswith(".ts"):
                    rel = f"{rel}.ts"
                if Path(rel).name in PROTECTED_FIXTURES:
                    continue
                dest = project_dir / "fixtures" / rel
                if dest.is_file():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(self._minimal_fixture_stub(rel), encoding="utf-8")
                file_rel = f"fixtures/{rel}"
                if file_rel not in written:
                    written.append(file_rel)
                created = True
            elif norm.startswith("../pages/"):
                class_name = Path(norm).name
                if not class_name.endswith("Page"):
                    class_name = f"{class_name}Page"
                dest = project_dir / "pages" / f"{class_name}.ts"
                if dest.is_file():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                if bank_portal and class_name in _BANK_PORTAL_PAGE_TEMPLATES:
                    content = self._read_page_template(class_name)
                else:
                    content = self._minimal_page_stub(class_name)
                dest.write_text(content, encoding="utf-8")
                file_rel = f"pages/{class_name}.ts"
                if file_rel not in written:
                    written.append(file_rel)
                created = True

        if bank_portal:
            login_page = project_dir / "pages" / "LoginPage.ts"
            if not login_page.is_file():
                self._ensure_bank_portal_pages(project_dir, written)
                created = True
            elif "async login()" not in login_page.read_text(encoding="utf-8", errors="ignore"):
                login_page.write_text(self._read_page_template("LoginPage"), encoding="utf-8")
                created = True
            fixture_path = project_dir / "fixtures" / "archivos" / "inscripciones-ach.txt"
            malformed_path = project_dir / "fixtures" / "archivos" / "malformed-inscripciones.txt"
            if not fixture_path.is_file() or not malformed_path.is_file():
                self._ensure_bank_portal_fixtures(project_dir, written)
                created = True

        return created

    def _scaffold_minimal_boilerplate(
        self, project_dir: Path, project_id: str, base_url: str | None, run_id: str
    ) -> List[str]:
        """Platform-owned Playwright shell — config + auth/evidence fixtures only."""
        written: List[str] = []
        target = self._origin_base_url(base_url)

        (project_dir / "fixtures").mkdir(parents=True, exist_ok=True)
        from src.services.project_env import merge_project_env

        merge_project_env(project_dir / ".env", {"BASE_URL": target, **({"APTP_RUN_ID": run_id} if run_id else {})})
        written.append(".env")

        pkg = (
            (TEMPLATES_DIR / "package.json.j2").read_text()
            if (TEMPLATES_DIR / "package.json.j2").exists()
            else self._default_package()
        )
        (project_dir / "package.json").write_text(
            pkg.replace("{{project_id}}", project_id).replace("{{playwright_version}}", PLAYWRIGHT_NPM_SPEC)
        )
        written.append("package.json")

        pw_config = self._read_template("playwright.config.ts.j2", project_id)
        (project_dir / "playwright.config.ts").write_text(pw_config or self._default_playwright_config())
        written.append("playwright.config.ts")

        for fixture_name in ("evidence.ts", "dynamic-auth.ts", "video-evidence-reporter.ts"):
            src = TEMPLATES_DIR / "fixtures" / fixture_name
            if src.is_file():
                shutil.copy(src, project_dir / "fixtures" / fixture_name)
                written.append(f"fixtures/{fixture_name}")

        if run_id:
            from src.automation.bank_portal import detect_bank_portal_project, sync_run_secrets_to_project_env

            bank_portal = detect_bank_portal_project(project_dir, target_url=base_url or "")
            sync_run_secrets_to_project_env(project_dir, run_id, bank_portal=bank_portal, base_url=base_url)

        return written

    def _write_llm_env_and_fixtures(self, project_dir: Path, codegen: Dict, written: List[str]) -> None:
        from src.services.project_env import merge_project_env

        env_block = codegen.get("env") or {}
        if env_block:
            merge_project_env(project_dir / ".env", {k: str(v) for k, v in env_block.items() if v is not None})

        for rel_path, content in (codegen.get("fixtures") or {}).items():
            rel_path = str(rel_path).lstrip("/").replace("\\", "/")
            if not rel_path or Path(rel_path).name in PROTECTED_FIXTURES:
                continue
            dest = project_dir / "fixtures" / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(content), encoding="utf-8")
            rel = f"fixtures/{rel_path}"
            if rel not in written:
                written.append(rel)

    def _write_llm_pages(
        self,
        project_dir: Path,
        test_plan: Dict,
        codegen: Dict,
        target: str,
        written: List[str],
        *,
        bank_portal: bool = False,
    ) -> None:
        page_hints = codegen.get("pages") or {}
        page_classes = {
            scenario.get("page_object")
            or (self._slug_for_spec(scenario.get("name", "scenario")).title().replace("-", "") + "Page")
            for scenario in test_plan.get("scenarios", [])
        }
        for page_class in page_hints:
            page_classes.add(page_class)
        if bank_portal:
            page_classes.add("LoginPage")

        for page_class in sorted(page_classes):
            if not page_class:
                continue
            hint = page_hints.get(page_class, {})
            rel = f"pages/{page_class}.ts"
            if bank_portal and page_class in _BANK_PORTAL_PAGE_TEMPLATES and not hint.get("source"):
                source = self._read_page_template(page_class)
            elif hint.get("source"):
                source = str(hint["source"])
            else:
                scenario = next(
                    (s for s in test_plan.get("scenarios", []) if s.get("page_object") == page_class),
                    {"locators": {"primary": "body"}, "steps": []},
                )
                source = self._generate_page(scenario, page_class, target, hint, bank_portal=bank_portal)
            if not self._is_valid_page_source(source):
                logger.warning("Invalid page source for %s; using method fallback", page_class)
                scenario = next(
                    (s for s in test_plan.get("scenarios", []) if s.get("page_object") == page_class),
                    {"locators": {"primary": "body"}, "steps": []},
                )
                if bank_portal and page_class in _BANK_PORTAL_PAGE_TEMPLATES:
                    source = self._read_page_template(page_class)
                else:
                    source = self._generate_page(scenario, page_class, target, {}, bank_portal=bank_portal)
            (project_dir / "pages" / f"{page_class}.ts").write_text(source, encoding="utf-8")
            if rel not in written:
                written.append(rel)

    def _generate_project(
        self,
        project_dir: Path,
        test_plan: Dict,
        project_id: str,
        base_url: str | None,
        codegen: Dict,
        run_id: str = "",
        exploration: Optional[Dict[str, Any]] = None,
        *,
        skip_bank_reconcile: bool = False,
    ) -> List[str]:
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "pages").mkdir(exist_ok=True)
        (project_dir / "fixtures").mkdir(exist_ok=True)
        (project_dir / "tests").mkdir(exist_ok=True)
        (project_dir / "reports").mkdir(exist_ok=True)
        (project_dir / "evidence").mkdir(exist_ok=True)

        written: List[str] = []
        target = self._origin_base_url(base_url)

        bank_portal = (
            test_plan.get("aut_profile") == "bank_portal"
            or (test_plan.get("suite") or {}).get("aut_profile") == "bank_portal"
        )
        if run_id:
            from src.services.run_cancellation import ensure_not_cancelled

            ensure_not_cancelled(run_id)
        if bank_portal and run_id:
            from src.automation.bank_portal import register_demo_secrets, register_no_token_demo_secrets

            register_demo_secrets(run_id)
            register_no_token_demo_secrets(run_id)

        written.extend(self._scaffold_minimal_boilerplate(project_dir, project_id, base_url, run_id))

        self._write_llm_env_and_fixtures(project_dir, codegen, written)
        self._write_llm_pages(project_dir, test_plan, codegen, target, written, bank_portal=bank_portal)

        spec_hints = codegen.get("specs") or {}

        planned_specs = set()
        for scenario in test_plan.get("scenarios", []):
            if scenario.get("playwright_skip"):
                continue
            sid = scenario.get("id", "")
            hint = spec_hints.get(sid, {})
            file_rel = hint.get("file") or f"{self._slug_for_spec(scenario.get('name', 'scenario'))}.spec.ts"
            planned_specs.add(Path(file_rel).name)

        tests_dir = project_dir / "tests"
        if tests_dir.is_dir():
            for stale in tests_dir.rglob("*.spec.ts"):
                if stale.name not in planned_specs:
                    stale.unlink()

        for scenario in test_plan.get("scenarios", []):
            if run_id:
                from src.services.run_cancellation import ensure_not_cancelled
                ensure_not_cancelled(run_id)
            if scenario.get("playwright_skip"):
                continue
            name = self._slug_for_spec(scenario.get("name", "scenario"))
            page_class = scenario.get("page_object") or (
                "".join(w.title() for w in name.split("-")) + "Page"
            )
            sid = scenario.get("id", "")
            spec_hint = spec_hints.get(sid, {})
            spec_rel = spec_hint.get("file") or f"{name}.spec.ts"
            spec_path = project_dir / "tests" / spec_rel
            spec_path.parent.mkdir(parents=True, exist_ok=True)

            spec_content = self._generate_spec(
                scenario, page_class, name, spec_hint, target, bank_portal=bank_portal
            )
            violations = reject_unsafe_generated_code(spec_content, filename=str(spec_path.relative_to(project_dir)))
            if violations:
                logger.warning("Codegen guardrail: %s — using template fallback", violations[0])
                spec_content = self._generate_spec(scenario, page_class, name, {}, target, bank_portal=bank_portal)
            spec_path.write_text(spec_content, encoding="utf-8")
            rel = f"tests/{spec_rel}"
            if rel not in written:
                written.append(rel)

        if bank_portal:
            self._ensure_bank_portal_pages(project_dir, written)
            self._ensure_bank_portal_fixtures(project_dir, written)
        self._scaffold_missing_local_imports(project_dir, written, bank_portal=bank_portal)

        if bank_portal and not skip_bank_reconcile:
            try:
                from src.automation.demo_qa_project import reconcile_demo_qa_project

                reconcile_demo_qa_project(
                    project_dir,
                    test_plan,
                    target_url=target,
                    run_id=run_id,
                )
            except Exception as exc:
                logger.warning("Bank portal reconcile skipped: %s", exc)

        return written

    def _read_template(self, name: str, project_id: str) -> str:
        path = TEMPLATES_DIR / name
        if path.exists():
            return path.read_text().replace("{{project_id}}", project_id)
        return ""

    def _default_package(self) -> str:
        return json.dumps(
            {
                "name": "aptp-generated-tests",
                "version": "1.0.0",
                "scripts": {"test": "playwright test"},
                "devDependencies": {
                    "@playwright/test": PLAYWRIGHT_NPM_SPEC,
                    "@types/node": "^20.0.0",
                    "typescript": "^5.3.0",
                },
            },
            indent=2,
        )

    def _default_playwright_config(self) -> str:
        return """import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  timeout: 60000,
  retries: 1,
  reporter: [
    ['json', { outputFile: 'reports/results.json' }],
    ['html', { outputFolder: 'reports/html', open: 'never' }],
    ['list'],
    ['./fixtures/video-evidence-reporter.ts'],
  ],
  use: { baseURL: process.env.BASE_URL, trace: 'on-first-retry', screenshot: 'only-on-failure', video: 'on' },
});
"""

    @staticmethod
    def _slug_for_spec(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (name or "scenario").lower()).strip("-")
        return slug[:96] or "scenario"

    @staticmethod
    def _ts_string(value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _module_basename(module_path: str) -> str:
        path = module_path.replace("\\", "/").strip()
        name = path.rsplit("/", 1)[-1]
        return name.removesuffix(".ts").removesuffix(".js")

    @staticmethod
    def _parse_import_symbols(import_line: str) -> tuple[str, List[str], bool]:
        """Return (module_path, symbols, is_namespace)."""
        match = _IMPORT_FROM_RE.search(import_line)
        if not match:
            return "", [], False
        module = match.group("module")
        kind = match.group("kind").strip()
        if kind == "*":
            return module, [], True
        sym_match = _IMPORT_SYMBOLS_RE.search(kind)
        if not sym_match:
            return module, [], False
        symbols = [s.strip() for s in sym_match.group(1).split(",") if s.strip()]
        return module, symbols, False

    @classmethod
    def _normalize_extra_imports(cls, raw_imports: List[Any], *, page_class: str | None = None) -> List[str]:
        """Turn LLM extra_imports into valid TypeScript import lines with deduped symbols."""
        lines: List[str] = []
        seen_symbols: set[str] = set()
        seen_lines: set[str] = set()

        def emit_line(line: str) -> None:
            normalized = line if line.endswith(";") else f"{line};"
            if normalized not in seen_lines:
                seen_lines.add(normalized)
                lines.append(normalized)

        def emit_named(module: str, symbols: List[str]) -> None:
            unique = [s for s in symbols if s and s not in seen_symbols]
            if not unique:
                return
            seen_symbols.update(unique)
            emit_line(f"import {{ {', '.join(unique)} }} from '{module}'")

        def emit_namespace(module: str) -> None:
            ns = cls._module_basename(module).replace("-", "_")
            if not ns or not re.match(r"^[A-Za-z_]\w*$", ns):
                ns = "fixtureNs"
            emit_line(f"import * as {ns} from '{module}'")

        def resolve_fixture_module(text: str) -> str:
            module = text.removesuffix(".ts").removesuffix(".js")
            if module.startswith("../") or module.startswith("./"):
                return module
            return f"../fixtures/{cls._module_basename(module)}"

        for item in raw_imports or []:
            text = str(item).strip()
            if not text:
                continue
            if "@playwright/test" in text:
                continue

            if text.startswith("import "):
                module, symbols, is_ns = cls._parse_import_symbols(text)
                if not module:
                    continue
                norm = module.replace("\\", "/")
                if page_class and f"pages/{page_class}" in norm:
                    continue
                if is_ns:
                    emit_namespace(module)
                elif symbols:
                    filtered = [s for s in symbols if s != "captureStepEvidence"]
                    if filtered:
                        emit_named(module, filtered)
                continue

            norm_path = text.replace("\\", "/")
            if page_class and f"pages/{page_class}" in norm_path:
                continue

            basename = cls._module_basename(norm_path)
            if basename in _SKIPPED_IMPORT_MODULES:
                continue
            if basename.endswith("Page"):
                emit_named(f"../pages/{basename}", [basename])
                continue

            module = resolve_fixture_module(norm_path)
            exports = _FIXTURE_MODULE_EXPORTS.get(basename)
            if exports:
                emit_named(module, exports)
            elif basename == "flow-helpers":
                emit_namespace(module)
            elif "/" in norm_path or norm_path.startswith("."):
                emit_namespace(module)
            else:
                continue

        return lines

    @staticmethod
    def _dedupe_evidence_imports(import_block: str) -> str:
        """Keep a single canonical captureStepEvidence import from ../fixtures/evidence."""
        if not import_block:
            return ""
        kept: List[str] = []
        evidence_seen = False
        for line in import_block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            module, symbols, is_ns = PlaywrightGeneratorAgent._parse_import_symbols(stripped)
            if symbols and "captureStepEvidence" in symbols:
                if evidence_seen:
                    remaining = [s for s in symbols if s != "captureStepEvidence"]
                    if remaining:
                        kept.append(f"import {{ {', '.join(remaining)} }} from '{module}';")
                else:
                    evidence_seen = True
                    kept.append("import { captureStepEvidence } from '../fixtures/evidence';")
            else:
                kept.append(stripped if stripped.endswith(";") else f"{stripped};")
        return "\n".join(kept)

    @staticmethod
    def _rewrite_bank_portal_before_each(lines: List[str]) -> List[str]:
        """Rewrite bare login() calls to LoginPage.login() pattern."""
        rewritten: List[str] = []
        for line in lines:
            if re.search(r"\bawait\s+login\s*\(\s*\)", line):
                rewritten.append("const loginPage = new LoginPage(page);")
                rewritten.append("await loginPage.login();")
            elif re.search(r"(?<![.\w])login\s*\(\s*\)", line) and "LoginPage" not in line:
                rewritten.append("const loginPage = new LoginPage(page);")
                rewritten.append("await loginPage.login();")
            else:
                rewritten.append(line)
        return rewritten

    def _generate_spec(
        self,
        scenario: Dict,
        page_class: str,
        name: str,
        hint: Dict,
        target: str,
        *,
        bank_portal: bool = False,
    ) -> str:
        bank_portal = bank_portal or scenario.get("aut_profile") == "bank_portal" or any(
            t for t in (scenario.get("tags") or []) if str(t).startswith("EMPRESARIAL")
        )
        is_manual = scenario.get("manual") or scenario.get("automation_tier") == "manual"
        extra_lines = self._normalize_extra_imports(hint.get("extra_imports") or [], page_class=page_class)
        body_lines = hint.get("test_body_lines")
        uses_template_steps = not body_lines
        if is_manual and not body_lines:
            manual_note = self._ts_string(
                (scenario.get("cp_code") or scenario.get("title") or scenario.get("name") or "Caso manual del intake")[:200]
            )
            body_lines = [f"test.skip(true, 'Operación manual — {manual_note}');"]
            uses_template_steps = False
        elif not body_lines:
            body_lines = self._steps_to_spec_ts(
                scenario, page_class, target, bank_portal=bank_portal, skip_login=bank_portal
            )
        body = "\n    ".join(body_lines)
        tags = scenario.get("tags") or []
        tag_line = ""
        if tags:
            tag_line = f"  // tags: {', '.join(str(t) for t in tags)}\n"
        describe_title = self._ts_string(scenario.get("cp_code") or scenario.get("name", name))
        ext_id = scenario.get("external_id") or scenario.get("id", "TC-001")
        label = scenario.get("cp_code") or scenario.get("name", name)
        if str(label).startswith("CP_"):
            test_title = self._ts_string(label)
        else:
            test_title = self._ts_string(f"{ext_id}: {label}")
        import_block = self._dedupe_evidence_imports("\n".join(extra_lines) if extra_lines else "")
        if page_class and f"pages/{page_class}" not in import_block.replace("\\", "/"):
            import_block = f"import {{ {page_class} }} from '../pages/{page_class}';\n{import_block}".strip()
        if "captureStepEvidence" not in import_block and "captureStepEvidence" in body:
            import_block = (import_block + "\nimport { captureStepEvidence } from '../fixtures/evidence';").strip()
        if bank_portal and ("LoginPage" in body or uses_template_steps) and "LoginPage" not in import_block:
            import_block += "\nimport { LoginPage } from '../pages/LoginPage';"
        init_line = ""
        if uses_template_steps and "pom." in body:
            init_line = f"const pom = new {page_class}(page);\n    "
        before_each = ""
        before_lines = hint.get("before_each_lines") or []
        if before_lines:
            if bank_portal:
                before_lines = self._rewrite_bank_portal_before_each(before_lines)
            body_before = "\n    ".join(before_lines)
            before_each = f"  test.beforeEach(async ({{ page }}) => {{\n    {body_before}\n  }});\n\n"
        elif bank_portal and uses_template_steps and not scenario.get("skip_auth"):
            before_each = "  test.beforeEach(async ({ page }) => {\n    const loginPage = new LoginPage(page);\n    await loginPage.login();\n  });\n\n"
        elif bank_portal and not before_lines and not is_manual and not scenario.get("skip_auth"):
            before_each = "  test.beforeEach(async ({ page }) => {\n    const loginPage = new LoginPage(page);\n    await loginPage.login();\n  });\n\n"
        return f"""import {{ test, expect }} from '@playwright/test';
{import_block}

test.describe('{describe_title}', () => {{
{tag_line}{before_each}  test('{test_title}', async ({{ page }}, testInfo) => {{
    test.setTimeout({hint.get("timeout_ms", 90000 if bank_portal else 60000)});
    {init_line}{body}
  }});
}});
"""

    def _steps_to_spec_ts(
        self,
        scenario: Dict,
        page_class: str,
        target: str,
        *,
        bank_portal: bool = False,
        skip_login: bool = False,
    ) -> List[str]:
        lines: List[str] = []
        steps = scenario.get("steps") or []
        skip_login = bank_portal and not scenario.get("skip_auth")
        if skip_login:
            steps = [
                s
                for s in steps
                if (s.get("action") or "").lower() != "login"
                and not (
                    (s.get("action") or "").lower() == "testid_visible"
                    and (s.get("target") or "") in {"reset-demo-data", "app_shell"}
                )
            ]
        uses_pom = not bank_portal and not any(
            (s.get("action") or "").lower() in {"login", "testid_click", "testid_fill", "upload", "testid_visible"}
            for s in steps
        )
        if uses_pom:
            lines.append("await pom.navigate();")

        if not steps:
            if uses_pom:
                lines.extend([
                    "await pom.executeScenario();",
                    "await captureStepEvidence(page, testInfo, 'step-final', 'Final state');",
                    "await expect(page).toHaveURL(/.+/);",
                ])
            return lines

        for i, step in enumerate(steps[:16]):
            action = (step.get("action") or "").lower()
            step_target = step.get("target", "")
            data = step.get("data", "")
            step_id = step.get("id") or f"step-{i + 1:02d}-{action}"
            label = f"{action} {step_target}".strip()
            lines.extend(self._emit_step_ts(action, step_target, data, step_id, label, target, bank_portal))
            if step.get("capture_evidence", True):
                lines.append(
                    f"await captureStepEvidence(page, testInfo, '{self._ts_string(step_id)}', '{self._ts_string(label)}');"
                )

        for assertion in (scenario.get("assertions") or [])[:4]:
            atype = (assertion.get("type") or "visible").lower()
            expected = assertion.get("expected", "")
            step_id = f"assert-{atype}-{len(lines)}"
            if atype == "url":
                lines.append(f"await expect(page).toHaveURL({self._js_regex_literal(expected)});")
            elif atype == "visible":
                tgt = assertion.get("target", "body")
                lines.append(self._expect_visible_ts(tgt))
            elif atype == "text":
                tgt = assertion.get("target", "body")
                lines.append(
                    f"await expect(page.locator('{self._ts_string(tgt)}')).toContainText('{self._ts_string(expected)}');"
                )
            lines.append(
                f"await captureStepEvidence(page, testInfo, '{step_id}', 'Assertion {atype}');"
            )

        return lines

    @staticmethod
    def _js_regex_literal(value: str) -> str:
        """Build a valid JS RegExp literal from a plain string.

        Python's re.escape does NOT escape the '/' delimiter, so a value like
        '/inscripciones/crear' produced 'toHaveURL(//inscripciones/crear/)' which
        is a syntax error in JS. Escape every JS-regex special char including '/'.
        """
        raw = (value or "").strip()
        if not raw:
            return "/.+/"
        # Normalize any pre-existing escapes, then escape JS regex specials + '/'.
        normalized = raw.replace("\\/", "/")
        escaped = re.sub(r"([\\^$.|?*+()\[\]{}/])", r"\\\1", normalized)
        return f"/{escaped}/"

    def _expect_visible_ts(self, target: str) -> str:
        tid = self._testid_from_target(target)
        if tid:
            return f"await expect(page.getByTestId('{self._ts_string(tid)}')).toBeVisible();"
        return f"await expect(page.locator('{self._ts_string(target)}')).toBeVisible();"

    @staticmethod
    def _testid_from_target(target: str) -> str:
        target = (target or "").strip()
        if target.startswith("[data-testid="):
            m = re.search(r"data-testid=([^\]]+)", target)
            return (m.group(1) if m else "").strip("'\"")
        if target.startswith("testid:"):
            return target.split(":", 1)[1]
        if target and not target.startswith((".", "#", "[")):
            return target
        return ""

    def _emit_step_ts(
        self,
        action: str,
        step_target: str,
        data: str,
        step_id: str,
        label: str,
        target: str,
        bank_portal: bool,
    ) -> List[str]:
        lines: List[str] = []
        tid = self._testid_from_target(step_target)

        if action == "login":
            if bank_portal:
                lines.append("const loginPage = new LoginPage(page);")
                lines.append("await loginPage.login();")
            else:
                asset = data or "login_user"
                lines.append(f"await loginBankPortal(page, '{self._ts_string(asset)}');")
        elif action == "goto":
            if step_target == "path" and data:
                lines.append(f"await page.goto('{self._ts_string(data)}');")
            else:
                lines.append(f"await page.goto(process.env.BASE_URL || '{self._ts_string(data or target)}');")
        elif action in {"testid_click", "click"} and tid:
            if tid == "transferencia-destino-bbva":
                # Prefer unique testid (first BBVA option); avoid getByRole(/BBVA/) which matches 3 rows.
                lines.append(
                    "await page.getByTestId('transferencia-destino-bbva').first().click();"
                )
            else:
                lines.append(f"await page.getByTestId('{self._ts_string(tid)}').first().click();")
        elif action in {"testid_fill", "fill"} and tid:
            lines.append(f"await page.getByTestId('{self._ts_string(tid)}').fill('{self._ts_string(data)}');")
        elif action == "testid_visible" and tid:
            if bank_portal and tid in {"app-shell", "reset-demo-data"}:
                lines.append(
                    "await expect(page.locator('[data-testid=\"app-shell\"], [data-testid=\"reset-demo-data\"]').first()).toBeVisible({ timeout: 15000 });"
                )
            else:
                lines.append(
                    f"await expect(page.getByTestId('{self._ts_string(tid)}')).toBeVisible({{ timeout: 15000 }});"
                )
        elif action == "upload" and tid:
            from src.automation.bank_portal import PLAIN_FILE_FIXTURE

            file_path = data or PLAIN_FILE_FIXTURE
            lines.append(
                f"await page.getByTestId('{self._ts_string(tid)}').setInputFiles('{self._ts_string(file_path)}');"
            )
            if bank_portal and tid == "crear-archivo-input":
                lines.append(
                    "await expect(page.getByTestId('crear-procesar-archivo')).toBeEnabled({ timeout: 15000 });"
                )
        elif action == "fill_label":
            label = step_target
            lines.append(
                f"await page.getByLabel('{self._ts_string(label)}').fill('{self._ts_string(data)}');"
            )
        elif action == "fill_input_near_label":
            label = step_target
            lines.append(
                f"await page.locator('div:has(> label:has-text(\"{self._ts_string(label)}\")) input').fill('{self._ts_string(data)}');"
            )
        elif action == "click_by_text":
            text = self._ts_string(step_target)
            lines.append(
                f"await page.getByRole('button', {{ name: '{text}' }}).first().click();"
            )
        elif action == "click_option_for_proceso":
            lines.append(
                "await page.getByRole('option').filter({ hasText: numProceso }).first().click();"
            )
        elif action == "click_option_contains":
            pattern = self._ts_string(data or step_target or "")
            lines.append(
                f"await page.getByRole('option', {{ name: /{pattern}/i }}).first().click();"
            )
        elif action == "select_option_by_text":
            label = self._ts_string(step_target)
            value = self._ts_string(data or "")
            lines.append(
                f"await page.getByRole('combobox', {{ name: '{label}' }}).click();"
            )
            if value == "${numProceso}":
                lines.append(
                    "await page.getByRole('option').filter({ hasText: numProceso }).first().click();"
                )
            elif value.startswith("${") and value.endswith("}"):
                lines.append(
                    f"await page.getByRole('option', {{ name: {value[2:-1]} }}).first().click();"
                )
            else:
                lines.append(
                    f"await page.getByRole('option', {{ name: /{value}/i }}).first().click();"
                )
        elif action == "click":
            lines.append(f"await page.locator('{self._ts_string(step_target)}').first().click();")
        elif action == "fill":
            lines.append(
                f"await page.locator('{self._ts_string(step_target)}').fill('{self._ts_string(data)}');"
            )
        elif action == "wait":
            lines.append("await page.waitForLoadState('domcontentloaded');")
        elif action == "click_first_testid" and step_target:
            prefix = self._ts_string(step_target)
            if prefix in {"validar-", "actualizar-"}:
                lines.append(
                    f"await page.getByTestId(`{prefix}${{numProceso}}`).first().click({{ timeout: 15000 }});"
                )
            else:
                lines.append(
                    f"await page.locator('[data-testid^=\"{prefix}\"]').first().click({{ timeout: 15000 }});"
                )
        elif action == "seed_inscripcion":
            estado = self._ts_string(data or step_target or "Creada")
            lines.append(
                f"""const numProceso = await page.evaluate((estado) => {{
  if (window.__aptpDemo?.seedInscripcion) {{
    const insc = window.__aptpDemo.seedInscripcion({{ estado }});
    return insc.numProceso;
  }}
  const now = new Date().toISOString();
  const suffix = String(Date.now()).slice(-6);
  const proceso = `INS-${{suffix}}-001`;
  const insc = {{
    numProceso: proceso,
    nombreProceso: 'APTP Demo Seed',
    cantidad: 1,
    servicioDestino: 'ACH',
    origen: 'Digitar',
    detalles: [{{
      tipoId: 'NIT', numId: '8006007002', nombres: 'Servicios', apellidos: 'Generales SAS',
      referencia: 'REF-ACH-DEMO', tipoProducto: 'CUENTAS CORRIENTES OTROS BANCOS',
      numProducto: '1082869975', bancoDestino: 'BBVA', valorMax: 5000000,
      email: 'finanzas@servicios.co', contacto: 'Laura', cargo: 'Mesa de pagos'
    }}],
    estado,
    ...(estado === 'Rechazada' ? {{ motivoRechazo: 'Error de Comunicación inscripción no realizada' }} : {{}}),
    fechaCreacion: now,
    fechaActualizacion: now,
    createdBy: 'aptp-seed'
  }};
  const key = 'dav.inscripciones';
  const existing = JSON.parse(localStorage.getItem(key) || '[]');
  const filtered = existing.filter((i) => i && i.numProceso !== insc.numProceso);
  filtered.unshift(insc);
  localStorage.setItem(key, JSON.stringify(filtered));
  window.dispatchEvent(new StorageEvent('storage', {{ key }}));
  return proceso;
}}, '{estado}');"""
            )
        elif action == "demo_fill_first_proceso_filter":
            # Prefer seeded __aptpProceso when present; always use Playwright fill for React state.
            lines.append(
                """const __filterProceso = (typeof __aptpProceso !== 'undefined' && __aptpProceso)
  ? String(__aptpProceso)
  : await page.evaluate(() => {
  const items = JSON.parse(localStorage.getItem('dav.inscripciones') || '[]');
  return items[0]?.numProceso || '';
});
await expect(page.getByTestId('filter-proceso')).toBeVisible({ timeout: 15000 });
await page.getByTestId('filter-proceso').fill(String(__filterProceso));"""
            )
        elif action == "demo_evaluate":
            method = self._ts_string(step_target)
            payload = data or ""
            if method == "setDemoConfig":
                lines.append(
                    f"await page.evaluate((cfg) => window.__aptpDemo?.setDemoConfig(JSON.parse(cfg)), '{self._ts_string(payload)}');"
                )
            elif method in {"seedEnviadoOtroBanco", "advanceBusinessDays"}:
                if method == "seedEnviadoOtroBanco":
                    # Capture proceso id; filter fill happens via demo_fill_first_proceso_filter
                    # after triggerBusinessDayRejections so React state is updated with Playwright fill.
                    arg = self._ts_string(payload or "1")
                    lines.append(
                        f"const __aptpProceso = await page.evaluate((n) => window.__aptpDemo?.seedEnviadoOtroBanco(Number(n))?.numProceso, '{arg}');"
                    )
                else:
                    arg = self._ts_string(payload or "1")
                    lines.append(
                        f"await page.evaluate((n) => window.__aptpDemo?.{method}(Number(n)), '{arg}');"
                    )
            else:
                lines.append(f"await page.evaluate(() => window.__aptpDemo?.{method}());")
        elif action == "clear_session":
            lines.append(
                """await page.evaluate(() => {
  localStorage.removeItem('dav.session');
  sessionStorage.clear();
});"""
            )
        elif action == "login_negative":
            import os

            from src.automation.bank_portal import (
                BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS,
                BANK_DEMO_SECRET_ENV_KEYS,
            )

            def _demo_value(keys_map: Dict[str, str], logical_key: str, fallback: str = "") -> str:
                env_name = keys_map.get(logical_key, "")
                return (os.getenv(env_name, "") if env_name else "") or fallback

            variation = (data or "wrong_password").strip()
            if variation == "no_token_client":
                cliente = _demo_value(BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS, "no_token_client.cliente", "312")
                documento = _demo_value(BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS, "no_token_client.documento", "20242587")
                clave = _demo_value(BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS, "no_token_client.clave", "123456")
                token = _demo_value(BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS, "no_token_client.token", "")
            else:
                cliente = _demo_value(BANK_DEMO_SECRET_ENV_KEYS, "login_user.cliente", "312")
                documento = _demo_value(BANK_DEMO_SECRET_ENV_KEYS, "login_user.documento", "20242587")
                clave = _demo_value(BANK_DEMO_SECRET_ENV_KEYS, "login_user.clave", "123456")
                token = _demo_value(BANK_DEMO_SECRET_ENV_KEYS, "login_user.token", "63866019")
            if variation == "wrong_password":
                clave = "000000"
            elif variation == "unknown_user":
                documento = "99999999"
            elif variation == "empty_token":
                token = ""
            lines.extend([
                "await page.getByTestId('login-cliente').fill('" + self._ts_string(cliente) + "');",
                "await page.getByTestId('login-documento').fill('" + self._ts_string(documento) + "');",
                "await page.getByTestId('login-clave').fill('" + self._ts_string(clave) + "');",
            ])
            if variation != "empty_token":
                lines.append(
                    "await page.getByTestId('login-token').fill('" + self._ts_string(token) + "');"
                )
            else:
                lines.append("await page.getByTestId('login-token').fill('');")
            lines.append("await page.getByTestId('login-submit').click();")
        else:
            lines.append("await page.waitForTimeout(300);")
        return lines

    @staticmethod
    def _sanitize_method_body(name: str, body: str) -> str:
        """Strip duplicate async method wrappers returned by the LLM."""
        text = (body or "").strip()
        if text.endswith(";"):
            text = text[:-1].strip()
        match = _NESTED_METHOD_BODY.match(text)
        if match and match.group("name") == name.split("(")[0]:
            text = match.group("body").strip()
        generic = _NESTED_METHOD_BODY.match(text)
        if generic:
            text = generic.group("body").strip()
        return text

    @staticmethod
    def _normalize_page_methods(methods: List[Dict]) -> List[Dict[str, str]]:
        """Drop LLM hallucinations such as async constructors or methodName()()."""
        normalized: List[Dict[str, str]] = []
        seen_names: set[str] = set()
        for method in methods or []:
            raw_name = str(method.get("name") or "").strip()
            if not raw_name or raw_name == "constructor":
                continue
            name = raw_name.split("(")[0].strip()
            if not name or not re.match(r"^[A-Za-z_]\w*$", name) or name in seen_names:
                continue
            body = PlaywrightGeneratorAgent._sanitize_method_body(name, str(method.get("body") or ""))
            if not body or f"async {name}" in body:
                continue
            seen_names.add(name)
            normalized.append({"name": name, "body": body})
        return normalized

    @classmethod
    def _is_valid_page_source(cls, source: str) -> bool:
        return not any(pattern.search(source) for pattern in _INVALID_PAGE_PATTERNS)

    @staticmethod
    def _primary_locator_expr(primary: str) -> str:
        tid = PlaywrightGeneratorAgent._testid_from_target(primary)
        if tid:
            return f"this.page.getByTestId('{tid}')"
        if primary and (primary.startswith("[") or primary.startswith(".") or primary.startswith("#")):
            return f"this.page.locator('{primary}')"
        return "this.page.locator('body')"

    def _default_page_methods(self, scenario: Dict, primary: str, target: str) -> str:
        root_expr = self._primary_locator_expr(primary)
        return f"""async navigate() {{
    await this.page.goto(process.env.BASE_URL || '{target}');
    await this.page.waitForLoadState('domcontentloaded');
  }}

  async executeScenario() {{
    const root = {root_expr};
    await root.first().waitFor({{ state: 'visible', timeout: 15000 }});
    {self._steps_to_ts(scenario.get('steps', []))}
  }}"""

    def _generate_page(
        self,
        scenario: Dict,
        page_class: str,
        target: str,
        hint: Dict,
        *,
        bank_portal: bool = False,
    ) -> str:
        if bank_portal and page_class in _BANK_PORTAL_PAGE_TEMPLATES and not hint.get("source"):
            return self._read_page_template(page_class)

        if hint.get("source"):
            return str(hint["source"])

        loc = scenario.get("locators") or {}
        primary = loc.get("primary", "body")
        methods = self._normalize_page_methods(hint.get("methods") or [])
        extra_imports = self._normalize_extra_imports(hint.get("imports") or [], page_class=page_class)
        import_block = "\n".join(extra_imports)
        if import_block:
            import_block = f"{import_block}\n"
        needs_expect = any("expect(" in m["body"] for m in methods)
        expect_import = ", expect" if needs_expect else ""

        method_bodies = "\n\n  ".join(
            f"async {m['name']}() {{\n    {m['body']}\n  }}"
            for m in methods
        ) or self._default_page_methods(scenario, primary, target)

        source = f"""import {{ Page{expect_import} }} from '@playwright/test';
{import_block}
export class {page_class} {{
  constructor(protected page: Page) {{}}

  {method_bodies}
}}
"""
        if not self._is_valid_page_source(source):
            logger.warning("Invalid LLM page object for %s; using template fallback", page_class)
            method_bodies = self._default_page_methods(scenario, primary, target)
            source = f"""import {{ Page }} from '@playwright/test';

export class {page_class} {{
  constructor(protected page: Page) {{}}

  {method_bodies}
}}
"""
        return source

    def _steps_to_ts(self, steps: List[Dict]) -> str:
        lines = []
        for step in steps[:10]:
            action = (step.get("action") or "").lower()
            target = step.get("target", "")
            data = step.get("data", "")
            if action == "goto":
                lines.append(f"await this.page.goto(process.env.BASE_URL || '{data or target}');")
            elif action == "click":
                lines.append(f"await this.page.locator('{target}').first().click();")
            elif action == "fill":
                lines.append(f"await this.page.locator('{target}').fill('{data}');")
            elif action == "wait":
                lines.append("await this.page.waitForLoadState('networkidle');")
        return "\n    ".join(lines) if lines else "await this.page.waitForTimeout(500);"
