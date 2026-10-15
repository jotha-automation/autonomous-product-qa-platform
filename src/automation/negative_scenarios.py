"""Synthesize negative, boundary, and resilience test scenarios from flows and site exploration."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional


NEGATIVE_TEMPLATES: Dict[str, List[Dict[str, str]]] = {
    "login": [
        {
            "suffix": "invalid-password",
            "title": "Login con clave incorrecta",
            "scenario_type": "negative",
            "input_variation": "wrong_password",
            "expected_failure_mode": "authentication_error",
        },
        {
            "suffix": "invalid-document",
            "title": "Login con documento inexistente",
            "scenario_type": "negative",
            "input_variation": "unknown_user",
            "expected_failure_mode": "user_not_found",
        },
        {
            "suffix": "missing-token",
            "title": "Login sin token de seguridad",
            "scenario_type": "negative",
            "input_variation": "empty_token",
            "expected_failure_mode": "validation_error",
        },
    ],
    "transfer": [
        {
            "suffix": "zero-amount",
            "title": "Transferencia con monto cero",
            "scenario_type": "boundary",
            "input_variation": "amount_zero",
            "expected_failure_mode": "validation_error",
        },
        {
            "suffix": "no-enrollment",
            "title": "Transferencia sin inscripción seleccionada",
            "scenario_type": "negative",
            "input_variation": "missing_enrollment",
            "expected_failure_mode": "business_rule_block",
        },
    ],
    "payment": [
        {
            "suffix": "non-accepted-enrollment",
            "title": "Pago con inscripción no aceptada",
            "scenario_type": "negative",
            "input_variation": "invalid_state",
            "expected_failure_mode": "rejection",
        },
    ],
    "enrollment": [
        {
            "suffix": "malformed-file",
            "title": "Importación de archivo con formato inválido",
            "scenario_type": "negative",
            "input_variation": "malformed_input",
            "expected_failure_mode": "parse_error",
        },
        {
            "suffix": "skip-validation-step",
            "title": "Acceso directo sin validar inscripción previa",
            "scenario_type": "resilience",
            "input_variation": "state_skip",
            "expected_failure_mode": "guard_redirect_or_error",
        },
    ],
    "form": [
        {
            "suffix": "required-fields-empty",
            "title": "Envío con campos obligatorios vacíos",
            "scenario_type": "negative",
            "input_variation": "empty_required",
            "expected_failure_mode": "validation_error",
        },
        {
            "suffix": "invalid-format",
            "title": "Envío con formato de datos inválido",
            "scenario_type": "boundary",
            "input_variation": "invalid_format",
            "expected_failure_mode": "validation_error",
        },
    ],
    "session": [
        {
            "suffix": "unauthenticated-access",
            "title": "Acceso a ruta protegida sin sesión",
            "scenario_type": "security",
            "input_variation": "no_session",
            "expected_failure_mode": "redirect_to_login",
        },
    ],
}


def _flow_key(scenario: Dict[str, Any]) -> str:
    blob = " ".join(
        [
            str(scenario.get("title") or ""),
            str(scenario.get("name") or ""),
            str(scenario.get("module") or ""),
            " ".join(scenario.get("tags") or []),
        ]
    ).lower()
    if any(k in blob for k in ("login", "auth", "sesion", "sesión", "session")):
        return "login"
    if any(k in blob for k in ("transfer", "ach", "transferencia")):
        return "transfer"
    if any(k in blob for k in ("pago", "payment")):
        return "payment"
    if any(k in blob for k in ("inscripcion", "enrollment", "crear", "validar", "actualizar")):
        return "enrollment"
    if any(k in blob for k in ("form", "contact", "submit")):
        return "form"
    return "form"


def _negative_steps(base: Dict[str, Any], template: Dict[str, str]) -> List[str]:
    base_steps = list(base.get("steps") or [])
    variation = template.get("input_variation", "")
    if variation == "wrong_password":
        return base_steps + ["Ingresar credenciales con clave incorrecta", "Intentar autenticarse"]
    if variation == "unknown_user":
        return base_steps + ["Ingresar documento de usuario inexistente", "Intentar autenticarse"]
    if variation == "empty_token":
        return base_steps + ["Dejar token de seguridad vacío", "Intentar autenticarse"]
    if variation == "amount_zero":
        return base_steps + ["Ingresar monto cero o negativo", "Intentar confirmar operación"]
    if variation == "missing_enrollment":
        return base_steps + ["No seleccionar inscripción destino", "Intentar ejecutar transferencia"]
    if variation == "invalid_state":
        return base_steps + ["Seleccionar inscripción en estado no válido", "Intentar procesar pago"]
    if variation == "malformed_input":
        return base_steps + ["Cargar archivo con delimitadores o columnas inválidas", "Procesar importación"]
    if variation == "state_skip":
        return ["Navegar directamente a etapa posterior sin completar validaciones previas"]
    if variation == "no_session":
        return ["Abrir URL protegida sin iniciar sesión", "Verificar redirección o bloqueo"]
    if variation == "empty_required":
        return base_steps + ["Dejar campos obligatorios vacíos", "Intentar enviar formulario"]
    if variation == "invalid_format":
        return base_steps + ["Ingresar datos con formato inválido", "Intentar enviar formulario"]
    return base_steps + [f"Ejecutar variación adversa: {variation}"]


def _negative_expected(template: Dict[str, str]) -> List[str]:
    mode = template.get("expected_failure_mode", "error")
    mapping = {
        "authentication_error": "El sistema muestra mensaje de error de autenticación sin conceder acceso",
        "user_not_found": "El sistema indica que el usuario no existe o credenciales inválidas",
        "validation_error": "El sistema bloquea la operación y muestra validación de campos",
        "business_rule_block": "El sistema impide la operación por regla de negocio",
        "rejection": "La operación es rechazada con motivo visible",
        "parse_error": "El sistema reporta error de formato o datos inválidos",
        "guard_redirect_or_error": "El sistema redirige o muestra error por estado inválido",
        "redirect_to_login": "El usuario es redirigido al login o recibe acceso denegado",
    }
    return [mapping.get(mode, "El sistema maneja el escenario adverso de forma controlada")]


def synthesize_negative_scenarios(
    base_scenarios: List[Dict[str, Any]],
    *,
    site_exploration: Optional[Dict[str, Any]] = None,
    max_per_base: int = 3,
) -> List[Dict[str, Any]]:
    """Expand happy-path scenarios with adversarial variants."""
    expanded: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for base in base_scenarios:
        if not isinstance(base, dict):
            continue
        base_id = str(base.get("id") or f"TC-{len(expanded)+1:03d}")
        base_copy = copy.deepcopy(base)
        base_copy.setdefault("scenario_type", "happy_path")
        base_copy.setdefault("test_types", ["smoke" if base_copy.get("priority") == "P0" else "regression"])
        if base_id not in seen_ids:
            expanded.append(base_copy)
            seen_ids.add(base_id)

        if base.get("automation_tier") == "manual":
            continue

        flow = _flow_key(base)
        templates = list(NEGATIVE_TEMPLATES.get(flow, NEGATIVE_TEMPLATES["form"]))
        if site_exploration:
            for opp in site_exploration.get("negative_opportunities") or []:
                if not isinstance(opp, dict):
                    continue
                if flow in str(opp.get("flow", "")).lower() or flow == "form":
                    templates.append(
                        {
                            "suffix": opp.get("id", "explore-neg"),
                            "title": opp.get("title", "Escenario adversario explorado"),
                            "scenario_type": opp.get("scenario_type", "negative"),
                            "input_variation": opp.get("input_variation", "explored"),
                            "expected_failure_mode": opp.get("expected_failure_mode", "error"),
                        }
                    )

        for template in templates[:max_per_base]:
            neg_id = f"{base_id}-{template['suffix']}"
            if neg_id in seen_ids:
                continue
            seen_ids.add(neg_id)
            neg = copy.deepcopy(base)
            neg.update(
                {
                    "id": neg_id,
                    "title": f"{base.get('title', base_id)} — {template['title']}",
                    "scenario_type": template.get("scenario_type", "negative"),
                    "priority": "P1" if template.get("scenario_type") == "negative" else "P2",
                    "steps": _negative_steps(base, template),
                    "expected_results": _negative_expected(template),
                    "negative_paths": [template.get("input_variation", "")],
                    "test_types": [template.get("scenario_type", "negative"), "regression"],
                    "risk_reason": f"Adversarial coverage for {flow}",
                    "input_variation": template.get("input_variation"),
                    "expected_failure_mode": template.get("expected_failure_mode"),
                    "recovery_expectation": "System remains stable; no partial corrupt state",
                    "parent_scenario_id": base_id,
                }
            )
            expanded.append(neg)

    return expanded


def populate_negative_paths(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure obligation-derived scenarios include negative path metadata."""
    scenario = copy.deepcopy(scenario)
    if scenario.get("negative_paths"):
        return scenario
    flow = _flow_key(scenario)
    templates = NEGATIVE_TEMPLATES.get(flow, NEGATIVE_TEMPLATES["form"])
    scenario["negative_paths"] = [t.get("input_variation", "") for t in templates[:3] if t.get("input_variation")]
    scenario.setdefault("scenario_type", "happy_path")
    return scenario
