"""Site exploration agent — discover sitemap, forms, and negative testing opportunities."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.automation.bank_portal import ROUTES, is_bank_portal_context
from src.services.target_url import resolve_target_url

logger = logging.getLogger("site_exploration")


class SiteExplorationAgent(BaseAgent):
    """Discover sitemap, forms, and negative testing opportunities for the target URL.

    Memory contract:
      Reads: context["target_url"], interpreted_intake
      Writes: site_exploration
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        target_url = resolve_target_url(context) or context.get("target_url") or ""
        interpreted = context.get("interpreted_intake") or self.read_memory("interpreted_intake", {})
        obligations = interpreted.get("obligations") or []
        bank_portal = is_bank_portal_context(context, obligations=obligations, target_url=target_url)

        if bank_portal:
            result = await self._bank_exploration(target_url)
        elif target_url:
            result = await self._generic_exploration(target_url, context)
        else:
            result = {
                "skipped": True,
                "reason": "no_target_url",
                "routes": [],
                "page_inventory": [],
                "form_inventory": [],
                "negative_opportunities": self._generic_negative_opportunities(),
            }

        self.store_short_term_memory("site_exploration", result)
        return result

    async def _bank_exploration(self, target_url: str) -> Dict[str, Any]:
        routes = [{"path": path, "name": name} for name, path in ROUTES.items()]
        selector_reconciliation = await self._reconcile_bank_selectors(target_url)
        page_inventory = [
            {"route": "/login", "features": ["authentication", "2fa"], "auth_required": False},
            {"route": "/inscripciones/crear", "features": ["wizard", "file_import"], "auth_required": True},
            {"route": "/inscripciones/validar", "features": ["state_transition"], "auth_required": True},
            {"route": "/inscripciones/actualizar", "features": ["state_transition"], "auth_required": True},
            {"route": "/consultas", "features": ["search", "print"], "auth_required": True},
            {"route": "/transferencias", "features": ["ach", "amount_validation"], "auth_required": True},
            {"route": "/pagos", "features": ["enrollment_validation"], "auth_required": True},
        ]
        form_inventory = [
            {"route": "/login", "fields": ["cliente", "documento", "clave", "token"]},
            {"route": "/transferencias", "fields": ["inscripcion", "valor", "tipo"]},
            {"route": "/pagos", "fields": ["inscripcion", "valor", "valida_inscripcion"]},
        ]
        negative_opportunities = [
            {"id": "login-wrong-clave", "flow": "login", "title": "Clave incorrecta", "scenario_type": "negative", "input_variation": "wrong_password", "expected_failure_mode": "authentication_error"},
            {"id": "login-wrong-doc", "flow": "login", "title": "Documento inválido", "scenario_type": "negative", "input_variation": "unknown_user", "expected_failure_mode": "user_not_found"},
            {"id": "login-no-token-client", "flow": "login", "title": "Cliente sin token secundario", "scenario_type": "negative", "input_variation": "no_token_client", "expected_failure_mode": "authentication_error"},
            {"id": "transfer-zero", "flow": "transfer", "title": "Monto inválido en transferencia", "scenario_type": "boundary", "input_variation": "amount_zero", "expected_failure_mode": "validation_error"},
            {"id": "payment-reject", "flow": "payment", "title": "Pago con inscripción no aceptada", "scenario_type": "negative", "input_variation": "invalid_state", "expected_failure_mode": "rejection"},
            {"id": "session-guard", "flow": "session", "title": "Acceso sin sesión", "scenario_type": "security", "input_variation": "no_session", "expected_failure_mode": "redirect_to_login"},
            {"id": "enrollment-bad-file", "flow": "enrollment", "title": "Archivo plano malformado", "scenario_type": "negative", "input_variation": "malformed_input", "expected_failure_mode": "parse_error"},
        ]
        return {
            "target_url": target_url,
            "bank_portal": True,
            "site_map": routes,
            "routes": routes,
            "page_inventory": page_inventory,
            "form_inventory": form_inventory,
            "state_transitions": [
                "Creada -> Validada -> Actualizada -> Aceptada",
                "Creada -> Validada -> Actualizada -> Enviada a otro banco -> Rechazada",
            ],
            "negative_opportunities": negative_opportunities,
            "selector_reconciliation": selector_reconciliation,
            "skipped": False,
        }

    async def _reconcile_bank_selectors(self, target_url: str) -> Dict[str, Any]:
        """Walk the live bank portal to reconcile expected testids against actual DOM."""
        from src.services.playwright_explorer import exploration_enabled, _login_bank_portal, _collect_testids, _secrets_for_run
        from src.automation.bank_portal import TEST_IDS

        if not target_url or not exploration_enabled():
            return {"skipped": True, "reason": "no_target_url_or_exploration_disabled"}

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            logger.warning("Playwright not available for selector reconciliation: %s", exc)
            return {"skipped": True, "reason": "playwright_unavailable"}

        secrets = _secrets_for_run(None)
        reconciliation: Dict[str, Any] = {"target_url": target_url, "testids": {}, "skipped": False}
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await _login_bank_portal(page, target_url, secrets)
                    all_testids = await _collect_testids(page)
                    for key, expected in TEST_IDS.items():
                        if expected in all_testids:
                            reconciliation["testids"][key] = expected
                        else:
                            alt = next(
                                (tid for tid in all_testids if tid.replace("_", "-").lower() == expected.replace("_", "-").lower()),
                                None,
                            )
                            reconciliation["testids"][key] = alt or expected
                            if alt:
                                logger.info("Reconciled testid %s -> %s", expected, alt)
                    for route in ("/login", "/inscripciones/crear", "/inscripciones/validar", "/inscripciones/actualizar", "/consultas", "/pagos", "/transferencias"):
                        try:
                            await page.goto(f"{target_url.rstrip('/')}{route}", wait_until="domcontentloaded", timeout=10000)
                            await page.wait_for_timeout(500)
                            route_testids = await _collect_testids(page)
                            reconciliation.setdefault("routes", {})[route] = route_testids
                        except Exception as exc:
                            logger.warning("Selector reconciliation failed for %s: %s", route, exc)
                finally:
                    await browser.close()
        except Exception as exc:
            logger.warning("Live selector reconciliation failed: %s", exc)
            return {"skipped": True, "reason": str(exc)}
        return reconciliation

    async def _generic_exploration(self, target_url: str, context: Dict[str, Any]) -> Dict[str, Any]:
        run_id = context.get("run_id")
        exploration_log: List[str] = []
        routes = [{"path": "/", "name": "home"}]
        page_inventory: List[Dict[str, Any]] = []
        form_inventory: List[Dict[str, Any]] = []

        try:
            from src.services.playwright_explorer import exploration_enabled

            if exploration_enabled():
                from playwright.async_api import async_playwright

                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    page = await browser.new_page()
                    try:
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                        links = await page.evaluate(
                            """() => Array.from(document.querySelectorAll('a[href]'))
                            .map(a => a.getAttribute('href'))
                            .filter(Boolean).slice(0, 40)"""
                        )
                        for href in links or []:
                            routes.append({"path": href, "name": href})
                        forms = await page.evaluate(
                            """() => Array.from(document.querySelectorAll('form')).map((f, i) => ({
                                index: i,
                                action: f.getAttribute('action') || '',
                                fields: Array.from(f.querySelectorAll('input,select,textarea'))
                                    .map(el => el.getAttribute('name') || el.getAttribute('id') || el.tagName)
                                    .filter(Boolean)
                            }))"""
                        )
                        form_inventory = [{"route": target_url, "forms": forms}]
                        testids = await page.evaluate(
                            """() => Array.from(document.querySelectorAll('[data-testid]'))
                            .map(el => el.getAttribute('data-testid')).filter(Boolean)"""
                        )
                        page_inventory.append({"route": target_url, "testids": testids or []})
                        exploration_log.append(f"visited:{target_url}")
                    finally:
                        await browser.close()
        except Exception as exc:
            logger.warning("Generic site exploration failed: %s", exc)
            exploration_log.append(f"error:{exc}")

        return {
            "target_url": target_url,
            "bank_portal": False,
            "site_map": routes,
            "routes": routes,
            "page_inventory": page_inventory,
            "form_inventory": form_inventory,
            "negative_opportunities": self._generic_negative_opportunities(form_inventory),
            "exploration_log": exploration_log,
            "skipped": False,
        }

    def _generic_negative_opportunities(
        self, form_inventory: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        opportunities = [
            {"id": "auth-fail", "flow": "login", "title": "Autenticación fallida", "scenario_type": "negative", "input_variation": "wrong_password", "expected_failure_mode": "authentication_error"},
            {"id": "required-empty", "flow": "form", "title": "Campos obligatorios vacíos", "scenario_type": "negative", "input_variation": "empty_required", "expected_failure_mode": "validation_error"},
            {"id": "session-guard", "flow": "session", "title": "Acceso sin sesión", "scenario_type": "security", "input_variation": "no_session", "expected_failure_mode": "redirect_to_login"},
        ]
        for entry in form_inventory or []:
            for form in entry.get("forms") or []:
                if isinstance(form, dict) and form.get("fields"):
                    opportunities.append(
                        {
                            "id": f"form-invalid-{form.get('index', 0)}",
                            "flow": "form",
                            "title": "Datos inválidos en formulario detectado",
                            "scenario_type": "boundary",
                            "input_variation": "invalid_format",
                            "expected_failure_mode": "validation_error",
                        }
                    )
        return opportunities
