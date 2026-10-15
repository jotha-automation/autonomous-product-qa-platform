"""Execution agent — runs Playwright tests."""
import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.runtime_policy import RealDataRequiredError, ensure_playwright_live, use_mock_playwright_execution
from src.services.playwright_runtime import (
    browser_mismatch_actionable_message,
    check_npm_playwright_version,
    read_resolved_npm_playwright_version,
    stderr_indicates_browser_mismatch,
)
from src.services.run_cancellation import RunCancelledError


def _phase_timeout_s(name: str, default: int) -> int:
    """Per-phase subprocess time budget (seconds) from env, with a safe floor."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


class PlaywrightPhaseTimeout(Exception):
    """Raised when a Playwright subprocess phase exceeds its time budget."""

    def __init__(self, phase: str, timeout_s: int):
        self.phase = phase
        self.timeout_s = timeout_s
        super().__init__(f"{phase} timed out after {timeout_s}s (infra timeout budget exceeded)")


class ExecutionAgent(BaseAgent):
    """Run Playwright tests against the generated project.

    Memory contract:
      Reads: generated_code, project_path, target_url, context["metadata"], context["run_id"]
      Writes: execution_results (also execution_key: execution_results when run_id present)
    """

    def _use_live_playwright(self) -> bool:
        if use_mock_playwright_execution():
            return False
        return bool(shutil.which("npx") or shutil.which("npm"))

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        project_path = context.get("project_path") or self.read_memory("generated_code", default={}).get("project_path")
        if not project_path:
            return {"status": "skipped", "reason": "no_project_path", "passed": 0, "failed": 0}

        path = Path(project_path)
        base_url = (
            context.get("target_url")
            or (context.get("metadata") or {}).get("target_url")
        )
        from src.services.target_url import resolve_target_url

        base_url = resolve_target_url(context) or base_url

        if self._use_live_playwright():
            result = await self._run_playwright(path, base_url, run_id=context.get("run_id"))
        elif use_mock_playwright_execution():
            result = self._mock_execution(path, context, base_url)
        else:
            ensure_playwright_live()
            raise RealDataRequiredError(
                "Playwright live execution is required but npx/npm is unavailable. "
                "Use the Playwright Docker image or set APTP_MOCK_PLAYWRIGHT_EXECUTION=false."
            )

        exec_id = context.get("execution_id") or context.get("run_id")
        if exec_id:
            self.memory.write(self.memory.execution_key(exec_id, "execution_results"), result)
        self.store_short_term_memory("execution_results", result)
        return result

    def _mock_execution(self, path: Path, context: Dict, base_url: str | None) -> Dict[str, Any]:
        tests = list((path / "tests").glob("*.spec.ts")) if (path / "tests").exists() else []
        reports_dir = path / "reports"
        reports_dir.mkdir(exist_ok=True)

        simulate_failure = os.getenv("APTP_MOCK_EXECUTION_FAILURES", "false").lower() in {"1", "true", "yes"}

        run_id = context.get("run_id")
        executed_tests = []
        failures = []
        for i, spec in enumerate(tests, start=1):
            if run_id:
                from src.services.run_cancellation import ensure_not_cancelled
                ensure_not_cancelled(run_id)
            failed = simulate_failure and i == 1 and len(tests) > 0
            status = "failed" if failed else "passed"
            executed_tests.append(
                {
                    "id": f"TC-{i:03d}",
                    "title": spec.stem,
                    "file": str(spec.relative_to(path)),
                    "status": status,
                    "duration_ms": 100,
                }
            )
            if failed:
                failures.append(
                    {
                        "title": spec.stem,
                        "error": {
                            "message": "Timeout 15000ms waiting for locator('body') — element not visible",
                            "category": "locator",
                        },
                    }
                )

        failed_count = len(failures)
        passed_count = len(tests) - failed_count
        report = {
            "stats": {
                "expected": passed_count,
                "unexpected": failed_count,
                "skipped": 0,
                "duration": len(tests) * 100,
            },
            "suites": [
                {
                    "title": path.name,
                    "specs": [
                        {
                            "title": t["title"],
                            "tests": [{"status": "expected", "results": [{"status": "passed", "duration": 100}]}],
                        }
                        for t in executed_tests
                    ],
                }
            ],
        }
        (reports_dir / "results.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (reports_dir / "executed_tests.json").write_text(
            json.dumps(executed_tests, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if base_url:
            from src.services.project_env import merge_project_env, origin_base_url

            merge_project_env(path / ".env", {"BASE_URL": origin_base_url(base_url)})

        total = len(tests)
        return {
            "status": "failed" if failed_count else "completed",
            "mode": "mock",
            "passed": passed_count,
            "failed": failed_count,
            "total": total,
            "pass_rate": passed_count / total if total else 0.0,
            "duration_seconds": max(1.0, len(tests) * 0.1),
            "artifacts": {"screenshots": [], "traces": [], "videos": []},
            "failures": failures,
            "executed_tests": executed_tests,
            "results_file": str(reports_dir / "results.json"),
            "stderr": "mock: locator timeout on first spec" if failures else "",
        }

    def _infra_failure_result(
        self,
        message: str,
        *,
        category: str = "browser_mismatch",
        stderr: str = "",
    ) -> Dict[str, Any]:
        return {
            "status": "error",
            "mode": "live",
            "infra_error": True,
            "error_category": category,
            "error": message,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "pass_rate": 0.0,
            "duration_seconds": 0.0,
            "artifacts": {"screenshots": [], "traces": [], "videos": []},
            "failures": [
                {
                    "title": "infrastructure",
                    "error": {"message": message[:500], "category": category},
                }
            ],
            "executed_tests": [],
            "stderr": stderr[:2000],
        }

    async def _communicate(
        self, proc: "asyncio.subprocess.Process", phase: str, timeout_s: int
    ) -> tuple[bytes, bytes]:
        """Await proc.communicate() under a time budget; kill + raise on timeout."""
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise PlaywrightPhaseTimeout(phase, timeout_s)

    async def _probe_chromium_launch(self, path: Path, env: Dict[str, str]) -> Optional[str]:
        if use_mock_playwright_execution():
            return None
        script = (
            "const { chromium } = require('@playwright/test');"
            "chromium.launch({ timeout: 15000 })"
            ".then((browser) => browser.close())"
            ".then(() => process.exit(0))"
            ".catch((err) => { console.error(err.message || String(err)); process.exit(1); });"
        )
        proc = await asyncio.create_subprocess_exec(
            "node",
            "-e",
            script,
            cwd=str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await self._communicate(
            proc, "chromium launch probe", _phase_timeout_s("APTP_PLAYWRIGHT_PROBE_TIMEOUT_S", 120)
        )
        if proc.returncode == 0:
            return None
        err_text = self._strip_ansi(
            (stderr.decode(errors="replace") or stdout.decode(errors="replace")).strip()
        )
        resolved = read_resolved_npm_playwright_version(path)
        return browser_mismatch_actionable_message(err_text, resolved_npm=resolved)

    async def _run_playwright(self, path: Path, base_url: str | None, run_id: str | None = None) -> Dict[str, Any]:
        env = {**os.environ, "CI": "true", "HOME": os.environ.get("HOME", "/root")}
        if base_url:
            from src.services.project_env import merge_project_env, origin_base_url

            env["BASE_URL"] = origin_base_url(base_url)
            merge_project_env(path / ".env", {"BASE_URL": env["BASE_URL"]})
        if run_id:
            from src.automation.bank_portal import detect_bank_portal_project, sync_run_secrets_to_project_env
            from src.services.project_env import read_env_file

            bank_portal = detect_bank_portal_project(path, target_url=base_url or "")
            if bank_portal:
                from src.agents.playwright_generator import PlaywrightGeneratorAgent

                heal_agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
                healed_written: List[str] = []
                heal_agent._ensure_bank_portal_pages(path, healed_written)
                heal_agent._ensure_bank_portal_fixtures(path, healed_written)
                heal_agent._scaffold_missing_local_imports(path, healed_written, bank_portal=True)
            sync_run_secrets_to_project_env(
                path, run_id, bank_portal=bank_portal, base_url=base_url
            )
            for key, value in read_env_file(path / ".env").items():
                if key.startswith("APTP_SECRET_") or key in {"BASE_URL", "APTP_RUN_ID"}:
                    env[key] = value
        browsers_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
        if browsers_path:
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

        try:
            if run_id:
                from src.services.run_cancellation import ensure_not_cancelled
                ensure_not_cancelled(run_id)
            install_cmd = ["npm", "install", "--no-audit", "--no-fund"]
            proc = await asyncio.create_subprocess_exec(
                *install_cmd,
                cwd=str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _, install_err = await self._communicate(
                proc, "npm install", _phase_timeout_s("APTP_NPM_INSTALL_TIMEOUT_S", 300)
            )
            if proc.returncode != 0:
                return {
                    "status": "error",
                    "mode": "live",
                    "error": f"npm install failed: {self._strip_ansi(install_err.decode(errors='replace')[:1500])}",
                    "passed": 0,
                    "failed": 1,
                    "pass_rate": 0.0,
                    "executed_tests": [],
                }

            version_error = check_npm_playwright_version(path)
            if version_error:
                return self._infra_failure_result(version_error, stderr=version_error)

            launch_error = await self._probe_chromium_launch(path, env)
            if launch_error:
                return self._infra_failure_result(launch_error, stderr=launch_error)

            if run_id:
                ensure_not_cancelled(run_id)
            list_proc = await asyncio.create_subprocess_exec(
                "npx",
                "playwright",
                "test",
                "--list",
                cwd=str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            list_stdout, list_stderr = await self._communicate(
                list_proc,
                "playwright test --list",
                _phase_timeout_s("APTP_PLAYWRIGHT_LIST_TIMEOUT_S", 120),
            )
            if list_proc.returncode != 0:
                list_err_text = self._strip_ansi(
                    (list_stderr.decode(errors="replace") or list_stdout.decode(errors="replace"))[:2000]
                )
                failures = [
                    {
                        "title": "compile",
                        "error": {
                            "message": list_err_text.split("\n", 1)[0][:500],
                            "category": "compile",
                        },
                    }
                ]
                return {
                    "status": "failed",
                    "mode": "live",
                    "passed": 0,
                    "failed": 1,
                    "total": 0,
                    "pass_rate": 0.0,
                    "duration_seconds": 0.0,
                    "artifacts": {"screenshots": [], "traces": [], "videos": []},
                    "failures": failures,
                    "executed_tests": [],
                    "compile_errors": 1,
                    "stderr": list_err_text,
                    "stdout": list_stdout.decode(errors="replace")[:1500] if list_stdout else "",
                }

            if run_id:
                ensure_not_cancelled(run_id)
            proc = await asyncio.create_subprocess_exec(
                "npx",
                "playwright",
                "test",
                cwd=str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await self._communicate(
                proc, "playwright test", _phase_timeout_s("APTP_PLAYWRIGHT_TEST_TIMEOUT_S", 1800)
            )
            if not (path / "reports" / "results.json").is_file() and stdout:
                (path / "reports").mkdir(exist_ok=True)
                try:
                    (path / "reports" / "results.json").write_bytes(stdout)
                except OSError:
                    pass

            report = self._load_json_report(path)
            passed = int(report.get("stats", {}).get("expected", 0) or 0)
            failed = int(report.get("stats", {}).get("unexpected", 0) or 0)
            executed_tests = self._extract_executed_tests(report, path)
            compile_failures = self._compile_failure_count(report, path)
            if compile_failures and passed + failed == 0:
                failed = compile_failures
            total = passed + failed
            if total == 0 and executed_tests:
                failed = sum(1 for t in executed_tests if t.get("status") != "passed")
                passed = sum(1 for t in executed_tests if t.get("status") == "passed")
                total = len(executed_tests)

            reports_dir = path / "reports"
            reports_dir.mkdir(exist_ok=True)
            (reports_dir / "executed_tests.json").write_text(
                json.dumps(executed_tests, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            stderr_text = self._strip_ansi(stderr.decode(errors="replace")[:2000]) if stderr else ""
            if stderr_indicates_browser_mismatch(stderr_text) and passed == 0 and failed > 0:
                resolved = read_resolved_npm_playwright_version(path)
                infra_msg = browser_mismatch_actionable_message(stderr_text, resolved_npm=resolved)
                return {
                    **self._infra_failure_result(infra_msg, stderr=stderr_text),
                    "failed": failed,
                    "total": total,
                    "executed_tests": executed_tests,
                    "results_file": str(reports_dir / "results.json"),
                    "stdout": self._strip_ansi(stdout.decode(errors="replace")[:1500]) if stdout else "",
                }

            run_ok = proc.returncode == 0 and failed == 0 and compile_failures == 0

            return {
                "status": "completed" if run_ok else "failed",
                "mode": "live",
                "passed": passed,
                "failed": failed,
                "total": total,
                "pass_rate": passed / total if total else 0.0,
                "duration_seconds": report.get("stats", {}).get("duration", 0) / 1000,
                "artifacts": self._collect_artifacts(path),
                "failures": self._extract_failures(report) or self._failures_from_errors(report),
                "executed_tests": executed_tests,
                "compile_errors": compile_failures,
                "results_file": str(reports_dir / "results.json"),
                "stderr": stderr_text,
                "stdout": self._strip_ansi(stdout.decode(errors="replace")[:1500]) if stdout else "",
            }
        except RunCancelledError:
            raise
        except PlaywrightPhaseTimeout as e:
            return self._infra_failure_result(str(e), category="timeout", stderr=str(e))
        except Exception as e:
            return {"status": "error", "mode": "live", "error": str(e), "passed": 0, "failed": 1, "pass_rate": 0.0, "executed_tests": []}

    def _load_json_report(self, path: Path) -> Dict[str, Any]:
        report_path = path / "reports" / "results.json"
        if report_path.is_file():
            try:
                return json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {}

    def _compile_failure_count(self, report: Dict, path: Path) -> int:
        errors = report.get("errors") or []
        syntax_errors = [
            e for e in errors if "SyntaxError" in (e.get("message") or "")
        ]
        if syntax_errors:
            return len(syntax_errors)
        if errors and not self._iter_specs(report):
            specs = list((path / "tests").glob("*.spec.ts"))
            return len(specs)
        return 0

    def _error_message_for_spec(self, report: Dict, spec_name: str) -> str:
        for err in report.get("errors") or []:
            loc = err.get("location") or {}
            file_path = loc.get("file") or err.get("message") or ""
            if spec_name in str(file_path):
                message = err.get("message") or ""
                return message.split("\n", 1)[0][:500]
        return ""

    @staticmethod
    def _iter_specs(report: Dict) -> List[Dict[str, Any]]:
        """Flatten Playwright's JSON report, recursing into nested suites.

        Playwright nests one suite per spec file and may wrap specs under child
        suites (suites[].suites[].specs[]). A non-recursive scan misses every
        spec and leaves test statuses as 'unknown'.
        """
        specs: List[Dict[str, Any]] = []

        def _walk(suite: Dict[str, Any]) -> None:
            for spec in suite.get("specs", []) or []:
                specs.append(spec)
            for child in suite.get("suites", []) or []:
                _walk(child)

        for suite in report.get("suites", []) or []:
            _walk(suite)
        return specs

    @staticmethod
    def _result_status(test: Dict[str, Any]) -> tuple[str, str]:
        """Map a Playwright test entry to (normalized_status, error_message)."""
        results = test.get("results") or [{}]
        last = results[-1] if results else {}
        raw = (last.get("status") or test.get("status") or "").lower()
        error = (last.get("error") or {}).get("message") or ""
        for res in results:
            if (res.get("error") or {}).get("message"):
                error = res["error"]["message"]
                break
        if raw in {"passed", "expected"}:
            return "passed", ""
        if raw == "skipped":
            return "skipped", ""
        return "failed", ExecutionAgent._clean_error_message(error)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re

        return re.sub(r"\x1b\[[0-9;]*m", "", text or "")

    @staticmethod
    def _clean_error_message(msg: str) -> str:
        from src.reporting.error_sanitization import sanitize_playwright_error

        return sanitize_playwright_error(ExecutionAgent._strip_ansi(msg), max_len=500)

    def _extract_executed_tests(self, report: Dict, path: Path) -> List[Dict[str, Any]]:
        from src.reporting.execution_recovery import (
            evidence_key_from_title,
            obligation_id_from_title,
        )

        executed: List[Dict[str, Any]] = []
        idx = 1
        for spec in self._iter_specs(report):
            for test in spec.get("tests", []) or []:
                status, error = self._result_status(test)
                results = test.get("results") or [{}]
                title = spec.get("title") or f"test-{idx}"
                obl_id = obligation_id_from_title(str(title))
                executed.append(
                    {
                        "id": obl_id or f"TC-{idx:03d}",
                        "title": title,
                        "file": spec.get("file", ""),
                        "status": status,
                        "duration_ms": results[-1].get("duration", 0) if results else 0,
                        "error": (error.split("\n", 1)[0][:500] if error else None),
                        "obligation_id": obl_id,
                        "evidence_key": evidence_key_from_title(str(title)),
                    }
                )
                idx += 1
        if not executed:
            for spec in sorted((path / "tests").rglob("*.spec.ts")):
                err_msg = self._error_message_for_spec(report, spec.name)
                rel = str(spec.relative_to(path))
                executed.append(
                    {
                        "id": f"TC-{idx:03d}",
                        "title": spec.stem,
                        "file": rel,
                        "status": "failed" if err_msg else "unknown",
                        "error": err_msg or None,
                    }
                )
                idx += 1
        elif report.get("stats", {}).get("unexpected", 0):
            for item in executed:
                if item.get("status") == "unknown":
                    item["status"] = "failed"
        return executed

    def _failures_from_errors(self, report: Dict) -> List[Dict]:
        failures = []
        for err in report.get("errors") or []:
            loc = err.get("location") or {}
            message = err.get("message") or ""
            if "SyntaxError" not in message and "No tests found" not in message:
                continue
            failures.append(
                {
                    "title": Path(str(loc.get("file", "compile"))).name,
                    "error": {"message": message.split("\n", 1)[0][:500], "category": "compile"},
                }
            )
        return failures

    def _collect_artifacts(self, path: Path) -> Dict[str, List[str]]:
        artifacts = {"screenshots": [], "traces": [], "videos": []}
        for pattern, key in [("**/*.png", "screenshots"), ("**/trace.zip", "traces"), ("**/*.webm", "videos")]:
            for f in path.glob(pattern):
                if "node_modules" not in f.parts:
                    artifacts[key].append(str(f))
        return artifacts

    def _extract_failures(self, report: Dict) -> List[Dict]:
        failures = []
        for spec in self._iter_specs(report):
            for test in spec.get("tests", []) or []:
                status, error = self._result_status(test)
                if status == "failed":
                    results = test.get("results") or [{}]
                    err_obj = (results[-1].get("error") if results else None) or {}
                    if not err_obj and error:
                        err_obj = {"message": error}
                    failures.append(
                        {
                            "title": spec.get("title"),
                            "error": err_obj,
                        }
                    )
        return failures
