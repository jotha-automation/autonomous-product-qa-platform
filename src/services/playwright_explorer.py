"""Live Playwright browser exploration — authenticate, navigate, discover selectors."""
from __future__ import annotations

import logging
import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("playwright_explorer")

_SKIP = os.getenv("APTP_SKIP_PLAYWRIGHT_EXPLORE", "false").lower() in {"1", "true", "yes"}
_STEP_TIMEOUT_MS = int(os.getenv("APTP_EXPLORE_STEP_TIMEOUT_MS", "8000") or "8000")
_SCENARIO_TIMEOUT_SEC = int(os.getenv("APTP_EXPLORE_SCENARIO_TIMEOUT_SEC", "90") or "90")


def _explore_max_scenarios() -> int:
    raw = (os.getenv("APTP_EXPLORE_MAX_SCENARIOS") or "12").strip()
    try:
        return max(1, min(64, int(raw)))
    except ValueError:
        return 12


def exploration_enabled() -> bool:
    if _SKIP:
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _normalize_base(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        url = f"https://{url}"
        parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _secrets_for_run(run_id: Optional[str]) -> Dict[str, str]:
    if not run_id:
        from src.automation.bank_portal import bank_demo_secrets

        return dict(bank_demo_secrets())
    from src.services.run_secret_store import RunSecretStore

    store = RunSecretStore.for_run(run_id)
    keys = store.list_keys()
    if keys:
        return {k: store.get(k) for k in keys}
    from src.automation.bank_portal import bank_demo_secrets

    return dict(bank_demo_secrets())


async def _collect_testids(page) -> List[str]:
    return await page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-testid]'))
        .map(el => el.getAttribute('data-testid'))
        .filter(Boolean)"""
    )


async def _login_bank_portal(page, base_url: str, secrets: Dict[str, str]) -> None:
    await page.goto(urljoin(base_url, "/login"), wait_until="domcontentloaded", timeout=30000)
    cliente = secrets.get("login_user.cliente", "312")
    documento = secrets.get("login_user.documento", "20242587")
    clave = secrets.get("login_user.clave", "123456")
    token = secrets.get("login_user.token", "63866019")

    hook_ready = False
    try:
        await page.wait_for_function(
            "() => Boolean(window.__aptpDemo?.login)",
            timeout=30000,
        )
        hook_ready = True
    except Exception:
        await page.get_by_test_id("login-page").wait_for(state="visible", timeout=30000)

    if hook_ready:
        result = await page.evaluate(
            """({ cliente, documento, clave, token }) => {
              const hook = window.__aptpDemo?.login;
              if (!hook) return { ok: false, error: 'APT login hook missing' };
              return hook(cliente, 'Cédula de Ciudadanía', documento, clave, token || undefined);
            }""",
            {"cliente": cliente, "documento": documento, "clave": clave, "token": token},
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Login failed")
    else:
        await page.get_by_test_id("login-cliente").fill(cliente)
        await page.get_by_test_id("login-documento").fill(documento)
        await page.get_by_test_id("login-clave").fill(clave)
        await page.get_by_test_id("login-token").fill(token)
        await page.get_by_test_id("login-submit").click()

    if not re.search(r"/(inscripciones|consultas|pagos|dashboard)", page.url or ""):
        await page.goto(urljoin(base_url, "/dashboard"), wait_until="domcontentloaded", timeout=30000)
    await page.get_by_test_id("reset-demo-data").wait_for(state="visible", timeout=30000)


async def _logged_in_bank_portal(page) -> bool:
    try:
        url = page.url or ""
        if re.search(r"/(inscripciones|consultas|pagos|dashboard)", url):
            return True
        return await page.get_by_test_id("crear-page").is_visible(timeout=1500)
    except Exception:
        return False


async def _apply_step(
    page, base_url: str, step: Dict[str, Any], secrets: Dict[str, str]
) -> Tuple[Dict[str, Any], List[str]]:
    """Execute one plan step; return possibly corrected step and visible testids."""
    action = (step.get("action") or "").lower()
    target = step.get("target") or ""
    data = step.get("data") or ""
    corrected = deepcopy(step)
    testids: List[str] = []

    if action == "login":
        if await _logged_in_bank_portal(page):
            testids = await _collect_testids(page)
            return corrected, testids
        await _login_bank_portal(page, base_url, secrets)
        testids = await _collect_testids(page)
        return corrected, testids

    if action == "goto":
        path = data if target == "path" and data else target
        if path.startswith("http"):
            await page.goto(path)
        else:
            await page.goto(urljoin(base_url, path))
        await page.wait_for_load_state("domcontentloaded")
        testids = await _collect_testids(page)
        return corrected, testids

    if action in {"testid_visible", "testid_click", "testid_fill", "upload"}:
        tid = _testid_from_target(target)
        locator = page.get_by_test_id(tid) if tid else page.locator(target)
        visible = False
        try:
            visible = await locator.first.is_visible(timeout=3000)
        except Exception:
            visible = False

        if not visible and tid:
            testids = await _collect_testids(page)
            alt = _best_testid_match(tid, testids)
            if alt:
                logger.info("Explorer remapped testid %s -> %s", tid, alt)
                corrected["target"] = alt
                tid = alt
                locator = page.get_by_test_id(tid)

        if action == "testid_visible":
            await page.get_by_test_id(tid).wait_for(state="visible", timeout=_STEP_TIMEOUT_MS)
        elif action == "testid_click":
            await page.get_by_test_id(tid).click()
            await page.wait_for_load_state("domcontentloaded")
        elif action == "testid_fill":
            await page.get_by_test_id(tid).fill(data)
        elif action == "upload":
            from playwright.async_api import expect

            from src.automation.bank_portal import PLAIN_FILE_FIXTURE

            file_path = data or PLAIN_FILE_FIXTURE
            await page.get_by_test_id(tid).set_input_files(file_path)
            if tid == "crear-archivo-input":
                await expect(page.get_by_test_id("crear-procesar-archivo")).to_be_enabled(
                    timeout=_STEP_TIMEOUT_MS
                )
        testids = await _collect_testids(page)
        return corrected, testids

    if action == "manual":
        testids = await _collect_testids(page)
        return corrected, testids

    if action == "fill_label":
        label = target or data
        value = data if target else ""
        if target and data:
            label, value = target, data
        field = page.get_by_label(label, exact=False)
        await field.first.fill(value)
        testids = await _collect_testids(page)
        return corrected, testids

    if action == "wait":
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=_STEP_TIMEOUT_MS)
        except Exception:
            pass
        testids = await _collect_testids(page)
        return corrected, testids

    testids = await _collect_testids(page)
    return corrected, testids


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


def _best_testid_match(wanted: str, available: List[str]) -> Optional[str]:
    if wanted in available:
        return wanted
    wanted_norm = wanted.replace("_", "-").lower()
    for tid in available:
        if tid.replace("_", "-").lower() == wanted_norm:
            return tid
    for tid in available:
        if wanted_norm in tid.replace("_", "-").lower() or tid.replace("_", "-").lower() in wanted_norm:
            return tid
    return None


def _steps_to_page_methods(page_class: str, steps: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Build deterministic page-object methods from verified steps."""
    methods: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add(name: str, body: str) -> None:
        if name in seen:
            return
        seen.add(name)
        methods.append({"name": name, "body": body})

    for step in steps:
        action = (step.get("action") or "").lower()
        target = step.get("target") or ""
        data = step.get("data") or ""
        tid = _testid_from_target(target)

        if action == "goto" and target == "path":
            add("gotoRoute", f"await this.page.goto('{data}');")
        elif action == "testid_click" and tid:
            method = f"click{''.join(p.title() for p in re.split(r'[-_]', tid))}"
            add(method, f"await this.page.getByTestId('{tid}').click();")
        elif action == "testid_fill" and tid:
            method = f"fill{''.join(p.title() for p in re.split(r'[-_]', tid))}"
            add(method, f"await this.page.getByTestId('{tid}').fill('{data}');")
        elif action == "fill_label":
            label = target
            method = "fill" + "".join(p.title() for p in re.split(r"\s+", label)[:3])
            add(
                method,
                f"await this.page.getByLabel('{label}').fill('{data}');",
            )
        elif action == "upload" and tid:
            add(
                "uploadArchivo",
                f"await this.page.getByTestId('{tid}').setInputFiles(filePath);",
            )

    if page_class == "LoginPage":
        add(
            "login",
            """await this.page.goto('/login');
    await this.page.getByTestId('login-page').waitFor({ state: 'visible', timeout: 15000 });
    await this.page.getByTestId('login-cliente').fill(getAssetField('login_user', 'cliente'));
    await this.page.getByTestId('login-documento').fill(getAssetField('login_user', 'documento'));
    await this.page.getByTestId('login-clave').fill(getAssetField('login_user', 'clave'));
    await this.page.getByTestId('login-token').fill(getAssetField('login_user', 'token'));
    await this.page.getByTestId('login-submit').click();
    await this.page.waitForURL(/\\/(inscripciones|consultas|pagos|dashboard)/, { timeout: 20000 });""",
        )
    return methods


def build_codegen_from_exploration(
    exploration: Dict[str, Any], test_plan: Dict[str, Any]
) -> Dict[str, Any]:
    pages: Dict[str, Any] = {}
    specs: Dict[str, Any] = {}

    for scenario in exploration.get("scenarios") or test_plan.get("scenarios", []):
        page_class = scenario.get("page_object") or "Page"
        steps = scenario.get("steps") or []
        methods = _steps_to_page_methods(page_class, steps)
        if methods:
            imports = []
            if page_class == "LoginPage":
                imports.append("import { getAssetField } from '../fixtures/dynamic-auth';")
            pages[page_class] = {"imports": imports, "methods": methods}

        sid = scenario.get("id", "")
        body_lines = _scenario_to_spec_lines(scenario)
        if body_lines:
            extra = ["import { captureStepEvidence } from '../fixtures/evidence';"]
            specs[sid] = {
                "extra_imports": extra,
                "test_body_lines": body_lines,
                "timeout_ms": 90000,
            }

    return {"pages": pages, "specs": specs, "from_exploration": True}


def _scenario_to_spec_lines(scenario: Dict[str, Any]) -> List[str]:
    from src.agents.playwright_generator import PlaywrightGeneratorAgent

    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    page_class = scenario.get("page_object") or "Page"
    bank = scenario.get("aut_profile") == "bank_portal" or "bank-portal" in (scenario.get("tags") or [])
    target = scenario.get("target_url") or ""
    lines = agent._steps_to_spec_ts(scenario, page_class, target, bank_portal=bank)
    return lines


def build_explorer_inventory(
    base_url: str,
    discovered_testids: Dict[str, List[str]],
    *,
    bank_portal: bool = False,
) -> Dict[str, Any]:
    from src.automation.bank_portal import ROUTES, TEST_IDS

    if bank_portal:
        routes = [{"path": path, "name": name} for name, path in ROUTES.items()]
    else:
        routes = [{"path": base_url, "name": "home"}]
    test_ids: List[Dict[str, str]] = []
    if bank_portal:
        step_map = {
            "crear-digitar": "modo-carga",
            "crear-continuar-header": "encabezado",
            "crear-continuar-detalle": "detalle",
            "crear-archivo-input": "importar",
            "crear-procesar-archivo": "procesar",
            "validar-page": "validar",
            "actualizar-page": "actualizar",
            "consultas-page": "consultas",
        }
        route_map = {
            "login": "/login",
            "crear": "/inscripciones/crear",
            "validar": "/inscripciones/validar",
            "actualizar": "/inscripciones/actualizar",
            "consultas": "/consultas",
        }
        for key, tid in TEST_IDS.items():
            if key.endswith("_page") or key.startswith("crear_") or key.startswith("login_"):
                test_ids.append(
                    {
                        "id": tid,
                        "step": step_map.get(tid, key.replace("_", "-")),
                        "route": route_map.get(key.replace("_page", "").split("_")[0], "/"),
                    }
                )
    else:
        for page_name, ids in discovered_testids.items():
            for tid in ids:
                test_ids.append({"id": tid, "step": page_name, "route": base_url})

    flows = {}
    if bank_portal:
        flows = {
            "crear_manual": ["encabezado", "digitar", "detalle", "validar"],
            "crear_archivo": ["encabezado", "importar", "procesar", "validar"],
        }

    return {"routes": routes, "testIds": test_ids, "flows": flows}


def _module_key(scenario: Dict[str, Any]) -> str:
    if scenario.get("manual"):
        return "manual"
    tags = " ".join(scenario.get("tags") or []).lower()
    for module in ("crear", "validar", "actualizar", "consultas", "pagos", "login"):
        if module in tags or module in (scenario.get("name") or "").lower():
            return module
    page = (scenario.get("page_object") or "").lower()
    if "inscripcion" in page:
        return "crear"
    if "consulta" in page:
        return "consultas"
    return scenario.get("id") or scenario.get("name") or "smoke"


def select_scenarios_for_exploration(scenarios: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pick a representative subset so live exploration finishes in minutes, not hours."""
    cap = _explore_max_scenarios()
    if len(scenarios) <= cap:
        return list(scenarios)

    chosen: List[Dict[str, Any]] = []
    seen_modules: set[str] = set()

    for scenario in scenarios:
        if scenario.get("manual"):
            continue
        key = _module_key(scenario)
        if key in seen_modules:
            continue
        seen_modules.add(key)
        chosen.append(scenario)
        if len(chosen) >= cap:
            break

    if len(chosen) < cap:
        explored_ids = {s.get("id") for s in chosen}
        for scenario in scenarios:
            if scenario.get("manual") or scenario.get("id") in explored_ids:
                continue
            chosen.append(scenario)
            explored_ids.add(scenario.get("id"))
            if len(chosen) >= cap:
                break

    logger.info(
        "Explorer will walk %s/%s scenarios (cap=%s, modules=%s)",
        len(chosen),
        len(scenarios),
        cap,
        sorted(seen_modules),
    )
    return chosen


def merge_explored_scenarios(
    original: List[Dict[str, Any]], explored: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_id = {s.get("id"): s for s in explored if s.get("id")}
    merged: List[Dict[str, Any]] = []
    for scenario in original:
        sid = scenario.get("id")
        merged.append(by_id.get(sid, scenario))
    return merged


async def explore_test_plan(
    test_plan: Dict[str, Any],
    base_url: Optional[str],
    *,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Interactive browser session: login, walk scenario steps, collect selectors.
    Returns enriched test_plan + codegen hints. Skips gracefully when disabled.
    """
    if not exploration_enabled():
        return {"skipped": True, "reason": "exploration_disabled"}

    url = base_url or test_plan.get("target_url") or ""
    if not url:
        return {"skipped": True, "reason": "no_target_url"}

    scenarios = test_plan.get("scenarios") or []
    if not scenarios:
        return {"skipped": True, "reason": "no_scenarios"}

    bank_portal = (
        test_plan.get("aut_profile") == "bank_portal"
        or (test_plan.get("suite") or {}).get("aut_profile") == "bank_portal"
    )
    if not bank_portal:
        return await _explore_generic(url, scenarios, run_id)

    return await _explore_bank_portal(url, scenarios, run_id)


async def _explore_bank_portal(
    base_url: str, scenarios: List[Dict[str, Any]], run_id: Optional[str]
) -> Dict[str, Any]:
    import asyncio
    from playwright.async_api import async_playwright

    base = _normalize_base(base_url)
    secrets = _secrets_for_run(run_id)
    walk_scenarios = select_scenarios_for_exploration(scenarios)
    enriched_by_id: Dict[str, Dict[str, Any]] = {}
    all_testids: Dict[str, List[str]] = {}
    log: List[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await _login_bank_portal(page, base, secrets)
            log.append(f"login_ok:{page.url}")

            for index, scenario in enumerate(walk_scenarios, start=1):
                if run_id:
                    from src.services.run_cancellation import ensure_not_cancelled

                    ensure_not_cancelled(run_id)
                sid = scenario.get("id", f"scenario-{index}")
                logger.info(
                    "Explorer scenario %s/%s: %s",
                    index,
                    len(walk_scenarios),
                    sid,
                )
                sc = deepcopy(scenario)
                sc["aut_profile"] = "bank_portal"
                corrected_steps: List[Dict[str, Any]] = []
                route_testids: List[str] = []

                async def _walk_steps() -> None:
                    nonlocal corrected_steps, route_testids
                    for step in sc.get("steps") or []:
                        if (step.get("action") or "").lower() == "manual":
                            corrected_steps.append(step)
                            continue
                        try:
                            step_out, tids = await _apply_step(page, base, step, secrets)
                            corrected_steps.append(step_out)
                            route_testids.extend(tids)
                        except Exception as exc:
                            log.append(f"step_failed:{step.get('id')}:{exc}")
                            corrected_steps.append(step)

                try:
                    await asyncio.wait_for(_walk_steps(), timeout=_SCENARIO_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    log.append(f"scenario_timeout:{sid}")
                    logger.warning(
                        "Explorer timed out after %ss on scenario %s",
                        _SCENARIO_TIMEOUT_SEC,
                        sid,
                    )

                sc["steps"] = corrected_steps
                enriched_by_id[sid] = sc
                page_name = sc.get("page_object", "Page")
                all_testids[page_name] = sorted(
                    set((all_testids.get(page_name) or []) + route_testids)
                )
        finally:
            await browser.close()

    enriched = merge_explored_scenarios(scenarios, list(enriched_by_id.values()))
    plan = {
        "scenarios": enriched,
        "discovered_testids": all_testids,
        "exploration_log": log,
        "explored_scenario_count": len(walk_scenarios),
        "total_scenario_count": len(scenarios),
    }
    plan["inventory"] = build_explorer_inventory(base, all_testids, bank_portal=True)
    codegen = build_codegen_from_exploration(plan, {"scenarios": enriched})
    return {**plan, "codegen": codegen, "skipped": False}


async def _explore_generic(
    base_url: str, scenarios: List[Dict[str, Any]], run_id: Optional[str]
) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    base = _normalize_base(base_url)
    enriched: List[Dict[str, Any]] = []
    log: List[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(base)
            await page.wait_for_load_state("domcontentloaded")
            for scenario in scenarios:
                if run_id:
                    from src.services.run_cancellation import ensure_not_cancelled
                    ensure_not_cancelled(run_id)
                sc = deepcopy(scenario)
                corrected: List[Dict[str, Any]] = []
                for step in sc.get("steps") or []:
                    try:
                        step_out, _ = await _apply_step(page, base, step, {})
                        corrected.append(step_out)
                    except Exception as exc:
                        log.append(f"step_failed:{step.get('id')}:{exc}")
                        corrected.append(step)
                sc["steps"] = corrected
                enriched.append(sc)
        finally:
            await browser.close()

    plan = {"scenarios": enriched, "exploration_log": log}
    plan["inventory"] = build_explorer_inventory(base, {}, bank_portal=False)
    codegen = build_codegen_from_exploration(plan, {"scenarios": enriched})
    return {**plan, "codegen": codegen, "skipped": False}
