"""Bank QA portal (INC1353166) automation catalog — locators, routes, demo secrets."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# Logical secret keys → environment variable names (values live in .env / Key Vault only).
BANK_DEMO_SECRET_ENV_KEYS: Dict[str, str] = {
    "login_user.cliente": "APTP_DEMO_LOGIN_USER_CLIENTE",
    "login_user.tipo_id": "APTP_DEMO_LOGIN_USER_TIPO_ID",
    "login_user.documento": "APTP_DEMO_LOGIN_USER_DOCUMENTO",
    "login_user.clave": "APTP_DEMO_LOGIN_USER_CLAVE",
    "login_user.token": "APTP_DEMO_LOGIN_USER_TOKEN",
}

BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS: Dict[str, str] = {
    "no_token_client.cliente": "APTP_DEMO_NO_TOKEN_CLIENTE",
    "no_token_client.tipo_id": "APTP_DEMO_NO_TOKEN_TIPO_ID",
    "no_token_client.documento": "APTP_DEMO_NO_TOKEN_DOCUMENTO",
    "no_token_client.clave": "APTP_DEMO_NO_TOKEN_CLAVE",
    "no_token_client.token": "APTP_DEMO_NO_TOKEN_TOKEN",
}


def _demo_secrets_allowed() -> bool:
    return os.getenv("APTP_ALLOW_DEMO_SECRETS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_demo_secrets(env_keys: Dict[str, str]) -> Dict[str, str]:
    from src.services.secrets import get_secrets_service

    service = get_secrets_service()
    secrets: Dict[str, str] = {}
    for logical_key, env_name in env_keys.items():
        value = service.get_secret(env_name)
        if value or logical_key.endswith(".token"):
            secrets[logical_key] = value
    return secrets


def bank_demo_secrets() -> Dict[str, str]:
    return _load_demo_secrets(BANK_DEMO_SECRET_ENV_KEYS)


def bank_demo_no_token_secrets() -> Dict[str, str]:
    return _load_demo_secrets(BANK_DEMO_NO_TOKEN_SECRET_ENV_KEYS)

ROUTES = {
    "login": "/login",
    "inscripciones": "/inscripciones",
    "crear": "/inscripciones/crear",
    "validar": "/inscripciones/validar",
    "actualizar": "/inscripciones/actualizar",
    "consultas": "/consultas",
    "pagos": "/pagos",
    "transferencias": "/transferencias",
}

# Pipe-delimited ACH row aligned with bank-qa-portal crear wizard defaults.
PLAIN_FILE_FIXTURE = "fixtures/archivos/inscripciones-ach.txt"
MALFORMED_FILE_FIXTURE = "fixtures/archivos/malformed-inscripciones.txt"
PLAIN_FILE_SAMPLE = (
    "NIT|8006007002|Servicios|Generales SAS|REF-ACH-DEMO|"
    "CUENTAS CORRIENTES OTROS BANCOS|1082869975|BBVA|5000000|"
    "finanzas@servicios.co|Laura|Mesa de pagos"
)

TEST_IDS = {
    "login_page": "login-page",
    "login_cliente": "login-cliente",
    "login_tipo_id": "login-tipo-id",
    "login_documento": "login-documento",
    "login_clave": "login-clave",
    "login_token": "login-token",
    "login_submit": "login-submit",
    "reset_demo": "reset-demo-data",
    # Persistent authenticated layout marker — always visible after login.
    "app_shell": "app-shell",
    "crear_page": "crear-inscripcion-page",
    "crear_nombre_proceso": "crear-nombre-proceso",
    "crear_continuar_header": "crear-continuar-header",
    "crear_digitar": "crear-digitar",
    "crear_importar": "crear-importar-archivo",
    "crear_continuar_detalle": "crear-continuar-detalle",
    "crear_archivo_input": "crear-archivo-input",
    "crear_procesar_archivo": "crear-procesar-archivo",
    "crear_error": "crear-error",
    "crear_detalle_error": "crear-detalle-error",
    "crear_archivo_error": "crear-archivo-error",
    "validar_page": "validar-page",
    "actualizar_page": "actualizar-page",
    "consultas_page": "consultas-page",
    "filter_proceso": "filter-proceso",
    "consultas_validation_error": "consultas-validation-error",
    "buscar_procesos": "buscar-procesos",
    "consulta_detalle": "consulta-detalle",
    "motivo_rechazo": "motivo-rechazo",
    "login_error": "login-error",
    "business_days_param": "business-days-param",
    "apply_business_days": "apply-business-days",
    "qa_controls_panel": "qa-controls-panel",
    "simulate_weekend": "simulate-weekend",
    "advance_business_days": "advance-business-days",
    "seed_enviado_otro_banco": "seed-enviado-otro-banco",
    "trigger_business_day_rejection": "trigger-business-day-rejection",
    "pagos_page": "pagos-page",
    "procesar_pago": "procesar-pago",
    "pagos_select_proceso": "pagos-select-proceso",
    "pago_warning_rechazo": "pago-warning-rechazo",
    "pago_estado_rechazada": "pago-estado-rechazada",
    "transferencias_page": "transferencias-page",
    "procesar_transferencia": "procesar-transferencia",
    "transferencia_valor": "transferencia-valor",
    "transferencia_destino": "transferencia-destino",
    "transferencia_destino_bbva": "transferencia-destino-bbva",
    "transferencia_inscripcion": "transferencia-inscripcion",
    "transferencia_error": "transferencia-error",
}

_BANK_HINTS = re.compile(
    r"(empresarial[-\s]?\d+|inscripcion|portal\s+empresarial|inc1353166|ach|bank-qa)",
    re.I,
)


def is_bank_portal_context(
    context: Optional[Dict[str, Any]] = None,
    *,
    documents: Optional[List[dict]] = None,
    obligations: Optional[List[dict]] = None,
    target_url: str = "",
) -> bool:
    ctx = context or {}
    url = target_url or ctx.get("target_url") or (ctx.get("metadata") or {}).get("target_url") or ""
    if "bank" in url.lower() or "inscripcion" in url.lower():
        return True

    interpreted = ctx.get("interpreted_intake") or {}
    modules = (interpreted.get("environment") or {}).get("modules") or []
    if any("inscripcion" in str(m).lower() or "ach" in str(m).lower() for m in modules):
        return True

    obls = obligations or interpreted.get("obligations") or []
    for ob in obls[:8]:
        blob = f"{ob.get('title', '')} {' '.join(ob.get('steps') or [])}"
        if _BANK_HINTS.search(blob):
            return True
        if re.search(r"EMPRESARIAL[-\s]?\d+", blob, re.I):
            return True

    for doc in documents or ctx.get("documents") or []:
        text = (doc.get("raw_text") or "")[:8000]
        name = doc.get("filename") or ""
        if _BANK_HINTS.search(text) or _BANK_HINTS.search(name):
            return True
    return False


def _is_usable_secret_value(value: Optional[str]) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text != "[REDACTED]"


def register_demo_secrets(run_id: str) -> None:
    """Fill bank-portal demo credentials when intake/xlsx secrets are missing or unusable."""
    if not _demo_secrets_allowed():
        return
    from src.services.run_secret_store import RunSecretStore

    store = RunSecretStore.for_run(run_id)
    for key, value in bank_demo_secrets().items():
        if not value:
            continue
        if _is_usable_secret_value(store.get(key)):
            continue
        store.register(key, value, source="bank_portal_demo")


def _secret_env_key(key: str) -> str:
    return f"APTP_SECRET_{key.replace('.', '_').upper()}"


def bank_portal_env_defaults() -> Dict[str, str]:
    """Default APTP_SECRET_* entries for bank-portal demo runs."""
    if not _demo_secrets_allowed():
        return {}
    defaults: Dict[str, str] = {}
    for key, value in {**bank_demo_secrets(), **bank_demo_no_token_secrets()}.items():
        if value or key.endswith(".token"):
            defaults[_secret_env_key(key)] = value
    return defaults


def detect_bank_portal_project(project_dir: "Path", *, target_url: str = "") -> bool:
    """Detect bank-portal generated projects even when runtime context is sparse."""
    from pathlib import Path

    path = Path(project_dir)
    if is_bank_portal_context(target_url=target_url):
        return True
    if (path / "pages" / "LoginPage.ts").is_file():
        return True
    tests_dir = path / "tests"
    if tests_dir.is_dir():
        for spec in list(tests_dir.glob("*.spec.ts"))[:5]:
            try:
                if "LoginPage" in spec.read_text(encoding="utf-8"):
                    return True
            except OSError:
                continue
    return False


def sync_run_secrets_to_project_env(
    project_dir: "Path",
    run_id: str,
    *,
    bank_portal: bool = False,
    base_url: Optional[str] = None,
) -> Dict[str, str]:
    """Persist run-scoped secrets into the generated Playwright project .env."""
    from pathlib import Path

    from src.services.project_env import merge_project_env, origin_base_url
    from src.services.run_secret_store import RunSecretStore

    path = Path(project_dir)
    bank_portal = bank_portal or detect_bank_portal_project(path, target_url=base_url or "")
    if bank_portal:
        register_demo_secrets(run_id)
        register_no_token_demo_secrets(run_id)

    store = RunSecretStore.for_run(run_id)
    updates: Dict[str, str] = {f"APTP_RUN_ID": run_id}
    if base_url:
        updates["BASE_URL"] = origin_base_url(base_url)
    for key in store.list_keys():
        value = store.get(key)
        if _is_usable_secret_value(value):
            updates[_secret_env_key(key)] = str(value)

    if bank_portal:
        for env_key, default_value in bank_portal_env_defaults().items():
            if not _is_usable_secret_value(updates.get(env_key)):
                updates[env_key] = default_value

    return merge_project_env(path / ".env", updates)


def register_no_token_demo_secrets(run_id: str) -> None:
    """Register the secondary no-token demo client for negative login scenarios."""
    if not _demo_secrets_allowed():
        return
    from src.services.run_secret_store import RunSecretStore

    store = RunSecretStore.for_run(run_id)
    for key, value in bank_demo_no_token_secrets().items():
        if value or key.endswith(".token"):
            store.register(key, value, source="bank_portal_demo_no_token")


def pom_structure() -> Dict[str, Any]:
    return {
        "pages": [
            {"name": "LoginPage", "route": ROUTES["login"], "responsibilities": ["authenticate"]},
            {"name": "InscripcionesPage", "route": ROUTES["crear"], "responsibilities": ["create", "validate", "update"]},
            {"name": "ConsultasPage", "route": ROUTES["consultas"], "responsibilities": ["search processes"]},
            {"name": "ValidarPage", "route": ROUTES["validar"], "responsibilities": ["validate process"]},
            {"name": "ActualizarPage", "route": ROUTES["actualizar"], "responsibilities": ["update status"]},
        ],
        "fixtures": ["bank-portal-auth", "flow-helpers", "test-data", "evidence"],
    }


def suite_meta(inc_id: str = "INC1353166") -> Dict[str, Any]:
    return {
        "name": f"{inc_id}-bank-portal-e2e",
        "feature": "inscripciones-ach",
        "browser_matrix": ["chromium"],
        "aut_profile": "bank_portal",
    }
