"""Map TestLink / obligation prose to structured Playwright steps (bank portal)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.automation.bank_portal import PLAIN_FILE_FIXTURE, ROUTES, TEST_IDS
from src.intake.test_naming import format_case_code

_MODULE_PATTERNS = (
    ("login", re.compile(r"login|ingresar\s+(al\s+)?portal|acceso\s+empresarial|autentic", re.I)),
    ("consultas", re.compile(r"rechazad|d[ií]a[s]?\s+h[aá]bil|consulta|buscar\s+procesos|detalle", re.I)),
    ("crear", re.compile(r"crear|digitar|archivo\s+plano|importar|encabezado", re.I)),
    ("validar", re.compile(r"validar|validaci[oó]n", re.I)),
    ("actualizar", re.compile(r"actualizar|enviad[ao]\s+a\s+otro\s+banco", re.I)),
    ("pagos", re.compile(r"pago|pagado\s+parcial", re.I)),
    ("transferencias", re.compile(r"transferencia|movimiento\s+ach", re.I)),
)

_BUSINESS_DAY_HINTS = re.compile(
    r"d[ií]a[s]?\s+h[aá]bil(?:es)?|par[aá]metro\s*=\s*\d+|enviad[ao]\s+a\s+otro\s+banco",
    re.I,
)
_BUSINESS_DAYS_PARAM = re.compile(r"par[aá]metro\s*=\s*(\d+)\s*d[ií]a", re.I)
_WEEKEND_HINTS = re.compile(r"fin\s+de\s+semana|s[aá]bado|domingo", re.I)


def _blob(obl: Dict[str, Any]) -> str:
    parts = [obl.get("title", ""), obl.get("external_id", "")]
    parts.extend(obl.get("preconditions") or [])
    parts.extend(obl.get("steps") or [])
    parts.extend(obl.get("expected_results") or [])
    return " ".join(str(p) for p in parts).lower()


def _title_blob(obl: Dict[str, Any]) -> str:
    return " ".join(str(obl.get(field) or "") for field in ("title", "external_id")).lower()


def is_business_day_obligation(obl: Dict[str, Any]) -> bool:
    """Only treat obligations as business-day when the title/id explicitly says so."""
    title = _title_blob(obl)
    if _BUSINESS_DAYS_PARAM.search(title):
        return True
    return bool(_BUSINESS_DAY_HINTS.search(title))


def business_days_param_from_obligation(obl: Dict[str, Any], default: int = 1) -> int:
    match = _BUSINESS_DAYS_PARAM.search(obl.get("title") or "") or _BUSINESS_DAYS_PARAM.search(_blob(obl))
    if match:
        return max(1, min(2, int(match.group(1))))
    if re.search(r"\b2\s*d[ií]a", _blob(obl)):
        return 2
    return default


def detect_module(obl: Dict[str, Any]) -> str:
    title = _title_blob(obl)
    if re.search(r"procesos?\s+inscripci", title):
        return "consultas"
    title_rules = (
        ("validar", "validar"),
        ("actualizar", "actualizar"),
        ("consulta", "consultas"),
        ("rechazad", "consultas"),
        ("día hábil", "consultas"),
        ("dias habil", "consultas"),
        ("crear", "crear"),
        ("digitar", "crear"),
        ("archivo plano", "crear"),
        ("pago", "pagos"),
        ("transferencia", "transferencias"),
        ("login", "login"),
    )
    for needle, module in title_rules:
        if needle in title:
            return module
    text = _blob(obl)
    for name, pattern in _MODULE_PATTERNS:
        if pattern.search(text):
            return name
    if "inscripcion" in text:
        return "crear"
    return "smoke"


def _step(
    action: str,
    target: str,
    data: str = "",
    *,
    step_id: str,
    capture: bool = True,
) -> Dict[str, Any]:
    return {
        "id": step_id,
        "action": action,
        "target": target,
        "data": data,
        "capture_evidence": capture,
    }


def _login_steps(prefix: str = "01") -> List[Dict[str, Any]]:
    return [
        _step("login", "bank", "login_user", step_id=f"step-{prefix}-login"),
        _step("testid_visible", TEST_IDS["app_shell"], "", step_id=f"step-{prefix}-app-ready", capture=True),
    ]


def _consultas_assert_steps(prefix: str = "06", *, include_rechazo: bool = False) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = [
        _step("demo_fill_first_proceso_filter", "", "", step_id=f"step-{prefix}-fill-filter"),
        _step("testid_click", TEST_IDS["buscar_procesos"], "", step_id=f"step-{prefix}-buscar"),
        _step("click_first_testid", "consulta-row-", "", step_id=f"step-{prefix}-open-row"),
        _step("testid_visible", TEST_IDS["consulta_detalle"], "", step_id=f"step-{prefix}-detalle", capture=True),
    ]
    if include_rechazo:
        steps.append(
            _step("testid_visible", TEST_IDS["motivo_rechazo"], "", step_id=f"step-{prefix}-motivo", capture=True)
        )
    return steps


def _business_day_steps(obl: Dict[str, Any]) -> List[Dict[str, Any]]:
    param = business_days_param_from_obligation(obl)
    weekend = bool(_WEEKEND_HINTS.search(_blob(obl)))
    steps: List[Dict[str, Any]] = []
    steps.extend(_login_steps("01"))
    steps.append(_step("goto", "path", ROUTES["consultas"], step_id="step-02-goto-consultas"))
    steps.append(
        _step(
            "demo_evaluate",
            "setDemoConfig",
            f'{{"businessDaysParam":{param},"simulateBusinessDays":true}}',
            step_id="step-03-set-param",
        )
    )
    if weekend:
        steps.append(_step("demo_evaluate", "simulateWeekend", "", step_id="step-04-sim-weekend"))
        steps.append(
            _step(
                "demo_evaluate",
                "seedEnviadoOtroBanco",
                str(param),
                step_id="step-05-seed-enviado",
            )
        )
        steps.append(
            _step(
                "demo_evaluate",
                "triggerBusinessDayRejections",
                "",
                step_id="step-06-trigger-no-reject",
            )
        )
        steps.append(
            _step(
                "demo_evaluate",
                "advanceBusinessDays",
                "1",
                step_id="step-07-advance",
            )
        )
    else:
        steps.append(
            _step(
                "demo_evaluate",
                "seedEnviadoOtroBanco",
                str(param),
                step_id="step-04-seed-enviado",
            )
        )
    steps.append(
        _step(
            "demo_evaluate",
            "triggerBusinessDayRejections",
            "",
            step_id="step-08-trigger-reject",
            capture=True,
        )
    )
    steps.extend(_consultas_assert_steps("09", include_rechazo=True))
    return steps


def map_negative_login_steps(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    variation = str(scenario.get("input_variation") or "")
    steps: List[Dict[str, Any]] = [
        _step("goto", "path", ROUTES["login"], step_id="step-01-goto-login"),
        _step("testid_visible", TEST_IDS["login_page"], "", step_id="step-02-login-page"),
    ]
    if variation == "wrong_password":
        steps.append(_step("login_negative", "bank", "wrong_password", step_id="step-03-login-fail"))
    elif variation == "unknown_user":
        steps.append(_step("login_negative", "bank", "unknown_user", step_id="step-03-login-fail"))
    elif variation == "empty_token":
        steps.append(_step("login_negative", "bank", "empty_token", step_id="step-03-login-fail"))
    elif variation == "no_token_client":
        steps.append(_step("login_negative", "bank", "no_token_client", step_id="step-03-login-fail"))
    else:
        steps.append(_step("login_negative", "bank", variation or "wrong_password", step_id="step-03-login-fail"))
    steps.append(
        _step("testid_visible", TEST_IDS["login_error"], "", step_id="step-04-login-error", capture=True)
    )
    return steps


def map_enrollment_malformed_input_steps() -> List[Dict[str, Any]]:
    from src.automation.bank_portal import MALFORMED_FILE_FIXTURE

    steps: List[Dict[str, Any]] = []
    steps.extend(_login_steps("01"))
    steps.append(_step("goto", "path", ROUTES["crear"], step_id="step-02-goto-crear"))
    steps.append(_step("testid_visible", TEST_IDS["crear_page"], "", step_id="step-03-crear-page"))
    steps.append(
        _step(
            "testid_fill",
            TEST_IDS["crear_nombre_proceso"],
            "Demo APTP malformed",
            step_id="step-04-nombre",
        )
    )
    steps.append(_step("testid_click", TEST_IDS["crear_continuar_header"], "", step_id="step-05-header"))
    steps.append(_step("testid_click", TEST_IDS["crear_importar"], "", step_id="step-06-importar"))
    steps.append(
        _step(
            "upload",
            TEST_IDS["crear_archivo_input"],
            MALFORMED_FILE_FIXTURE,
            step_id="step-07-upload-malformed",
        )
    )
    steps.append(_step("testid_click", TEST_IDS["crear_procesar_archivo"], "", step_id="step-08-procesar"))
    steps.append(
        _step(
            "testid_visible",
            TEST_IDS["crear_archivo_error"],
            "",
            step_id="step-09-archivo-error",
            capture=True,
        )
    )
    return steps


def map_form_negative_steps(variation: str) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    steps.extend(_login_steps("01"))
    steps.append(_step("goto", "path", ROUTES["consultas"], step_id="step-02-goto-consultas"))
    steps.append(_step("testid_visible", TEST_IDS["consultas_page"], "", step_id="step-03-consultas-page"))
    fill_value = "   " if variation == "empty_required" else "BAD-FORMAT"
    steps.append(
        _step(
            "testid_fill",
            TEST_IDS["filter_proceso"],
            fill_value,
            step_id=f"step-04-fill-{variation}",
        )
    )
    steps.append(_step("testid_click", TEST_IDS["buscar_procesos"], "", step_id="step-05-buscar"))
    steps.append(
        _step(
            "testid_visible",
            TEST_IDS["consultas_validation_error"],
            "",
            step_id="step-06-validation-error",
            capture=True,
        )
    )
    return steps


def map_transfer_negative_steps(variation: str) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    steps.extend(_login_steps("01"))
    steps.append(_step("goto", "path", ROUTES["transferencias"], step_id="step-02-goto-transferencias"))
    steps.append(
        _step("testid_visible", TEST_IDS["transferencias_page"], "", step_id="step-03-transferencias-page")
    )
    if variation == "missing_enrollment":
        steps.append(
            _step(
                "testid_click",
                TEST_IDS["transferencia_destino"],
                "",
                step_id="step-04-open-destino",
            )
        )
        steps.append(
            _step(
                "testid_click",
                TEST_IDS["transferencia_destino_bbva"],
                "",
                step_id="step-05-pick-bbva-destino",
            )
        )
        steps.append(
            _step(
                "testid_fill",
                TEST_IDS["transferencia_valor"],
                "500000",
                step_id="step-05-valor",
            )
        )
    else:
        steps.append(
            _step(
                "testid_fill",
                TEST_IDS["transferencia_valor"],
                "0",
                step_id="step-04-valor-cero",
            )
        )
    steps.append(
        _step("testid_click", TEST_IDS["procesar_transferencia"], "", step_id="step-06-submit-transfer")
    )
    steps.append(
        _step(
            "testid_visible",
            TEST_IDS["transferencia_error"],
            "",
            step_id="step-07-transfer-error",
            capture=True,
        )
    )
    return steps


def map_payment_invalid_state_steps() -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    steps.extend(_login_steps("01"))
    steps.append(_step("seed_inscripcion", "Creada", "", step_id="step-02-seed-creada"))
    steps.append(_step("goto", "path", ROUTES["pagos"], step_id="step-03-goto-pagos"))
    steps.append(_step("testid_visible", TEST_IDS["pagos_page"], "", step_id="step-04-pagos-page"))
    steps.append(
        _step(
            "testid_click",
            TEST_IDS["pagos_select_proceso"],
            "",
            step_id="step-05-open-proceso-select",
        )
    )
    steps.append(
        _step(
            "click_option_for_proceso",
            "",
            "",
            step_id="step-06-select-proceso",
        )
    )
    steps.append(
        _step(
            "testid_visible",
            TEST_IDS["pago_warning_rechazo"],
            "",
            step_id="step-06-warning",
            capture=True,
        )
    )
    steps.append(_step("testid_click", TEST_IDS["procesar_pago"], "", step_id="step-07-submit-pago"))
    steps.append(
        _step(
            "testid_visible",
            TEST_IDS["pago_estado_rechazada"],
            "",
            step_id="step-08-rechazada",
            capture=True,
        )
    )
    return steps


def map_functional_scenario_steps(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map synthesized negative/boundary functional scenarios to Playwright steps."""
    scenario_type = scenario.get("scenario_type") or "happy_path"
    if scenario_type == "happy_path":
        obl_shape = {
            "id": scenario.get("id"),
            "title": scenario.get("title") or scenario.get("name"),
            "external_id": scenario.get("external_id"),
            "steps": scenario.get("steps") or [],
            "expected_results": scenario.get("expected_results") or [],
            "preconditions": scenario.get("preconditions") or [],
        }
        return map_obligation_to_playwright_steps(obl_shape)

    flow = _flow_key_from_scenario(scenario)
    variation = str(scenario.get("input_variation") or "")
    if variation == "no_session":
        module = str(scenario.get("module") or "").lower()
        protected_route = ROUTES["crear"] if module in {"session", "enrollment", "crear"} else ROUTES["actualizar"]
        return [
            _step("goto", "path", ROUTES["login"], step_id="step-00-goto-login"),
            _step("clear_session", "", "", step_id="step-00b-clear-session"),
            _step("goto", "path", protected_route, step_id="step-01-goto-protected"),
            _step("testid_visible", TEST_IDS["login_page"], "", step_id="step-02-login-redirect", capture=True),
        ]
    if variation == "state_skip":
        return [
            _step("goto", "path", ROUTES["login"], step_id="step-00-goto-login"),
            _step("clear_session", "", "", step_id="step-00b-clear-session"),
            _step("goto", "path", ROUTES["actualizar"], step_id="step-01-goto-protected"),
            _step("testid_visible", TEST_IDS["login_page"], "", step_id="step-02-login-redirect", capture=True),
        ]
    if flow == "login":
        return map_negative_login_steps(scenario)
    if flow == "enrollment" and variation == "malformed_input":
        return map_enrollment_malformed_input_steps()
    if flow == "form" and variation in {"empty_required", "invalid_format"}:
        return map_form_negative_steps(variation)
    if flow == "transfer" and variation in {"amount_zero", "missing_enrollment"}:
        return map_transfer_negative_steps(variation)
    if flow == "payment" and variation == "invalid_state":
        return map_payment_invalid_state_steps()
    return map_obligation_to_playwright_steps(
        {
            "id": scenario.get("id"),
            "title": scenario.get("title") or scenario.get("name"),
            "steps": scenario.get("steps") or [],
        }
    )


def _flow_key_from_scenario(scenario: Dict[str, Any]) -> str:
    from src.automation.negative_scenarios import _flow_key

    return _flow_key(scenario)


def map_obligation_to_playwright_steps(obl: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic bank-portal step plan from a single obligation."""
    if is_business_day_obligation(obl):
        return _business_day_steps(obl)

    module = detect_module(obl)
    ext = obl.get("external_id") or ""
    obl_id = obl.get("id") or "OBL-001"
    tag = re.search(r"(\d+)", ext or obl_id)
    num = tag.group(1) if tag else "01"

    steps: List[Dict[str, Any]] = []
    steps.extend(_login_steps("01"))

    if module == "crear":
        steps.append(_step("goto", "path", ROUTES["crear"], step_id="step-02-goto-crear"))
        steps.append(
            _step("testid_visible", TEST_IDS["crear_page"], "", step_id="step-02b-crear-page")
        )
        steps.append(
            _step(
                "testid_fill",
                TEST_IDS["crear_nombre_proceso"],
                "Demo APTP E2E",
                step_id="step-03-fill-nombre",
            )
        )
        steps.append(
            _step(
                "testid_click",
                TEST_IDS["crear_continuar_header"],
                "",
                step_id="step-04-continuar-header",
            )
        )
        if re.search(r"archivo|plano|import", _blob(obl)):
            steps.append(_step("testid_click", TEST_IDS["crear_importar"], "", step_id="step-03-mode-archivo"))
            steps.append(
                _step(
                    "upload",
                    TEST_IDS["crear_archivo_input"],
                    PLAIN_FILE_FIXTURE,
                    step_id="step-04-upload",
                )
            )
            steps.append(
                _step("testid_click", TEST_IDS["crear_procesar_archivo"], "", step_id="step-05-procesar")
            )
        else:
            steps.append(_step("testid_click", TEST_IDS["crear_digitar"], "", step_id="step-05-mode-digitar"))
            steps.append(
                _step(
                    "fill_input_near_label",
                    "N° Identificación",
                    "8006007002",
                    step_id="step-06-fill-num-id",
                )
            )
            steps.append(
                _step(
                    "fill_input_near_label",
                    "N° Producto / Cuenta",
                    "1082869975",
                    step_id="step-07-fill-num-producto",
                )
            )
            steps.append(
                _step("testid_click", TEST_IDS["crear_continuar_detalle"], "", step_id="step-08-continuar")
            )
        steps.append(
            _step("testid_visible", TEST_IDS["validar_page"], "", step_id="step-09-crear-landed-validar")
        )

    elif module == "validar":
        steps.append(_step("seed_inscripcion", "Creada", "", step_id="step-02-seed"))
        steps.append(_step("goto", "path", ROUTES["validar"], step_id="step-03-goto-validar"))
        steps.append(_step("testid_visible", TEST_IDS["validar_page"], "", step_id="step-04-validar-page"))
        steps.append(
            _step(
                "click_first_testid",
                "validar-",
                "",
                step_id="step-05-validar-proceso",
            )
        )

    elif module == "actualizar":
        steps.append(_step("seed_inscripcion", "Validada", "", step_id="step-02-seed"))
        steps.append(_step("goto", "path", ROUTES["actualizar"], step_id="step-03-goto-actualizar"))
        steps.append(_step("testid_visible", TEST_IDS["actualizar_page"], "", step_id="step-04-actualizar-page"))
        steps.append(
            _step(
                "click_first_testid",
                "actualizar-",
                "",
                step_id="step-05-actualizar-proceso",
            )
        )

    elif module == "consultas":
        estado_seed = "Rechazada" if re.search(r"rechazad", _blob(obl)) else "Aceptada"
        steps.append(_step("seed_inscripcion", estado_seed, "", step_id="step-02-seed"))
        steps.append(_step("goto", "path", ROUTES["consultas"], step_id="step-03-goto-consultas"))
        steps.append(_step("testid_visible", TEST_IDS["consultas_page"], "", step_id="step-04-consultas-page"))
        steps.extend(_consultas_assert_steps("05", include_rechazo=(estado_seed == "Rechazada")))

    elif module == "pagos":
        steps.append(_step("seed_inscripcion", "Aceptada", "", step_id="step-02-seed"))
        steps.append(_step("goto", "path", ROUTES["pagos"], step_id="step-03-goto-pagos"))
        steps.append(_step("testid_visible", TEST_IDS["pagos_page"], "", step_id="step-04-pagos-page"))
        steps.append(
            _step(
                "select_option_by_text",
                "Proceso de inscripción",
                "${numProceso}",
                step_id="step-05-select-proceso",
            )
        )
        steps.append(_step("testid_click", TEST_IDS["procesar_pago"], "", step_id="step-06-procesar-pago", capture=True))

    elif module == "transferencias":
        steps.append(_step("goto", "path", ROUTES["transferencias"], step_id="step-02-goto-transferencias"))
        steps.append(
            _step(
                "testid_visible",
                TEST_IDS["transferencias_page"],
                "",
                step_id="step-03-transferencias-page",
            )
        )
        steps.append(
            _step(
                "fill_input_near_label",
                "Valor (COP)",
                "500000",
                step_id="step-04-fill-valor",
            )
        )
        steps.append(
            _step(
                "click_by_text",
                "Procesar transferencia",
                "",
                step_id="step-05-procesar-transferencia",
                capture=True,
            )
        )

    else:
        steps.append(_step("goto", "path", ROUTES["inscripciones"], step_id="step-02-goto-home"))
        steps.append(_step("wait", "networkidle", "", step_id="step-03-ready"))

    return steps


def _scenario_name(obl: Dict[str, Any], *, index: int = 0) -> str:
    if obl.get("cp_code"):
        return str(obl["cp_code"])
    if index > 0:
        return format_case_code(index, str(obl.get("title") or obl.get("id") or ""))
    slug = (obl.get("title") or obl.get("id") or "scenario").replace(" ", "-").lower()[:80]
    return slug


def map_functional_scenario_to_scenario(scenario: Dict[str, Any], target_url: str, *, index: int = 0) -> Dict[str, Any]:
    """Build a Playwright scenario dict from a functional (incl. negative) test-design scenario."""
    obl_id = scenario.get("id") or "OBL-001"
    name = _scenario_name(scenario, index=index)
    steps = map_functional_scenario_steps(scenario)
    scenario_type = scenario.get("scenario_type") or "happy_path"
    variation = str(scenario.get("input_variation") or "")
    flow = _flow_key_from_scenario(scenario)
    evidence_steps = [s["id"] for s in steps if s.get("capture_evidence")]
    if scenario_type == "happy_path":
        primary = TEST_IDS["consultas_page"]
    elif variation == "malformed_input":
        primary = TEST_IDS["crear_archivo_error"]
    elif variation in {"empty_required", "invalid_format"}:
        primary = TEST_IDS["consultas_validation_error"]
    elif variation in {"amount_zero", "missing_enrollment"}:
        primary = TEST_IDS["transferencia_error"]
    elif variation == "invalid_state":
        primary = TEST_IDS["pago_estado_rechazada"]
    elif flow == "login":
        primary = TEST_IDS["login_error"]
    elif variation in {"no_session", "state_skip"}:
        primary = TEST_IDS["login_page"]
    else:
        primary = TEST_IDS["login_error"]
    skip_auth = scenario_type != "happy_path" and (
        flow in {"login", "session"} or variation in {"no_session", "state_skip"}
    )
    return {
        "id": obl_id,
        "name": name,
        "priority": scenario.get("priority") or "P1",
        "page_object": "LoginPage" if scenario_type != "happy_path" and _flow_key_from_scenario(scenario) == "login" else "ConsultasPage",
        "requirement_ids": scenario.get("requirement_ids") or [f"REQ-{obl_id}"],
        "preconditions": scenario.get("preconditions") or ["Bank portal reachable"],
        "evidence_steps": evidence_steps,
        "steps": steps,
        "assertions": [
            {"type": "visible", "target": f"[data-testid={primary}]", "expected": "true"},
        ],
        "locators": {"primary": f"[data-testid={primary}]", "fallback": "body"},
        "tags": list(scenario.get("tags") or []) + ["bank-portal", scenario_type],
        "external_id": scenario.get("external_id", ""),
        "automation_tier": scenario.get("automation_tier", "automated"),
        "cp_code": scenario.get("cp_code") or (name if name.startswith("CP_") else ""),
        "target_url": target_url,
        "scenario_type": scenario_type,
        "parent_scenario_id": scenario.get("parent_scenario_id"),
        "input_variation": variation or scenario.get("input_variation"),
        "skip_auth": skip_auth,
    }


def map_obligation_to_scenario(obl: Dict[str, Any], target_url: str, *, index: int = 0) -> Dict[str, Any]:
    module = detect_module(obl)
    ext = obl.get("external_id") or ""
    obl_id = obl.get("id") or "OBL-001"
    name = _scenario_name(obl, index=index)
    page_map = {
        "login": "LoginPage",
        "crear": "InscripcionesPage",
        "validar": "InscripcionesPage",
        "actualizar": "InscripcionesPage",
        "consultas": "ConsultasPage",
        "pagos": "ConsultasPage",
        "transferencias": "ConsultasPage",
        "smoke": "LoginPage",
    }
    steps = map_obligation_to_playwright_steps(obl)
    evidence_steps = [s["id"] for s in steps if s.get("capture_evidence")]
    if is_business_day_obligation(obl):
        primary = TEST_IDS["motivo_rechazo"]
    elif module == "consultas":
        primary = (
            TEST_IDS["motivo_rechazo"]
            if re.search(r"rechazad", _blob(obl))
            else TEST_IDS["consulta_detalle"]
        )
    else:
        primary = TEST_IDS.get(f"{module}_page", TEST_IDS["login_page"])
    assertion_tid = TEST_IDS["validar_page"] if module == "crear" else primary

    tags = ["regression", "bank-portal"]
    if ext:
        tags.append(ext.replace(" ", "-").upper())
    tier = obl.get("automation_tier", "partial")
    if tier in ("automated", "partial"):
        tags.append("smoke" if tier == "automated" else "regression")

    return {
        "id": obl_id,
        "name": name,
        "priority": "P0" if tier == "automated" else "P1",
        "page_object": page_map.get(module, "LoginPage"),
        "requirement_ids": [f"REQ-{obl_id}"],
        "acceptance_criteria_ids": [],
        "preconditions": obl.get("preconditions") or ["Bank portal reachable"],
        "evidence_steps": evidence_steps,
        "steps": steps,
        "assertions": [
            {"type": "visible", "target": f"[data-testid={assertion_tid}]", "expected": "true"},
        ],
        "locators": {"primary": f"[data-testid={assertion_tid}]", "fallback": "body"},
        "tags": tags,
        "external_id": ext,
        "automation_tier": tier,
        "cp_code": obl.get("cp_code") or (name if name.startswith("CP_") else ""),
        "target_url": target_url,
    }


def map_manual_obligation_scenario(obl: Dict[str, Any], target_url: str) -> Dict[str, Any]:
    """Legacy manual placeholder — business-day cases now use demo simulation steps."""
    if is_business_day_obligation(obl):
        scenario = map_obligation_to_scenario({**obl, "automation_tier": "automated"}, target_url)
    else:
        scenario = map_obligation_to_scenario(obl, target_url)
    scenario["manual"] = True
    scenario["playwright_skip"] = True
    return scenario


def automatable_obligations(obligations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """All obligations runnable in Playwright when portal demo hooks exist."""
    out: List[Dict[str, Any]] = []
    for obl in obligations:
        if obl.get("automation_tier") == "manual" and not is_business_day_obligation(obl):
            continue
        out.append(obl)
    return out
