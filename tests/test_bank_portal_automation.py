"""Bank portal Playwright automation — obligation mapping and spec generation."""
from pathlib import Path

import pytest

from src.agents.playwright_architect import PlaywrightArchitectAgent
from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.automation.bank_portal import (
    PLAIN_FILE_FIXTURE,
    ROUTES,
    TEST_IDS,
    is_bank_portal_context,
    register_demo_secrets,
    sync_run_secrets_to_project_env,
)
from src.automation.obligation_steps import (
    automatable_obligations,
    detect_module,
    map_obligation_to_playwright_steps,
    map_obligation_to_scenario,
)
from src.services.run_secret_store import RunSecretStore

FIXTURE = Path(__file__).parent / "fixtures" / "documents" / "inc1353166-testlink.txt"


@pytest.fixture
def sample_obligation():
    return {
        "id": "OBL-EMP-13330",
        "external_id": "EMPRESARIAL-13330",
        "title": "EMPRESARIAL-13330: Crear inscripción digitar",
        "steps": ["Ingresar Procesos de Inscripciones", "Seleccionar Digitar Inscripciones"],
        "expected_results": ["Visualiza proceso creado"],
        "automation_tier": "automated",
        "required_assets": ["login_user"],
    }


def test_detect_bank_portal_from_obligation(sample_obligation):
    assert is_bank_portal_context(obligations=[sample_obligation]) is True


def test_detect_module_crear(sample_obligation):
    assert detect_module(sample_obligation) == "crear"


def test_map_obligation_includes_login_and_testids(sample_obligation):
    steps = map_obligation_to_playwright_steps(sample_obligation)
    actions = [s["action"] for s in steps]
    assert "login" in actions
    assert "testid_fill" in actions
    assert "testid_click" in actions
    assert any(s["target"] == "crear-continuar-header" for s in steps)
    assert any(s["target"] == "crear-digitar" for s in steps)


def test_archivo_plano_obligation_uses_txt_fixture():
    obl = {
        "id": "OBL-EMP-13331",
        "title": "EMPRESARIAL-13331: Crear inscripción archivo plano",
        "steps": ["Importar archivo plano ACH"],
        "automation_tier": "automated",
    }
    steps = map_obligation_to_playwright_steps(obl)
    upload = next(s for s in steps if s["action"] == "upload")
    assert upload["data"] == "fixtures/archivos/inscripciones-ach.txt"


def test_generator_scaffolds_plain_file_fixture_without_codegen(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-plain-file",
    )
    fixture = tmp_path / "fixtures" / "archivos" / "inscripciones-ach.txt"
    assert fixture.is_file()
    assert "|" in fixture.read_text(encoding="utf-8")


def test_validar_obligation_seeds_and_clicks_first_row():
    obl = {
        "id": "OBL-EMP-13333",
        "title": "EMPRESARIAL-13333: Validar Inscripción",
        "steps": ["Validar proceso"],
        "automation_tier": "automated",
    }
    steps = map_obligation_to_playwright_steps(obl)
    actions = [s["action"] for s in steps]
    assert "seed_inscripcion" in actions
    assert "click_first_testid" in actions


def test_architect_bank_fallback_produces_scenarios(sample_obligation):
    agent = PlaywrightArchitectAgent.__new__(PlaywrightArchitectAgent)
    scenarios = [{"id": "OBL-EMP-13330", "title": sample_obligation["title"], "automation_tier": "automated"}]
    plan = agent._fallback(
        scenarios,
        "https://bank-demo.up.railway.app",
        obligations=[sample_obligation],
        bank_portal=True,
    )
    assert plan.get("aut_profile") == "bank_portal"
    assert len(plan["scenarios"]) == 1
    assert plan["scenarios"][0]["steps"][0]["action"] == "login"


def test_generator_bank_spec_has_login_and_evidence(sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    scenario["aut_profile"] = "bank_portal"
    spec = agent._generate_spec(
        scenario,
        scenario["page_object"],
        "crear-inscripcion-digitar",
        {},
        "https://bank-demo.up.railway.app",
        bank_portal=True,
    )
    assert "captureStepEvidence" in spec
    assert "getByTestId('crear-digitar')" in spec
    assert "getByTestId('crear-nombre-proceso')" in spec
    assert "LoginPage" in spec
    assert "test.beforeEach" in spec


def test_generator_minimal_scaffold_only(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = {
        "id": "OBL-EMP-13330",
        "name": "crear-digitar",
        "page_object": "InscripcionesPage",
        "steps": [{"id": "s1", "action": "login", "target": "bank", "data": "login_user"}],
        "tags": ["EMPRESARIAL-13330"],
    }
    codegen = {
        "pages": {
            "InscripcionesPage": {
                "source": "import { Page } from '@playwright/test';\nexport class InscripcionesPage { constructor(protected page: Page) {} }",
            },
            "LoginPage": {
                "source": "import { Page } from '@playwright/test';\nexport class LoginPage { constructor(protected page: Page) {} async login() {} }",
            },
        },
        "fixtures": {
            "bank-portal-auth.ts": "export async function loginBankPortal() {}",
            "archivos/inscripciones-ach.txt": "NIT|8006007002|Demo|User|REF|CUENTA|123|BBVA|1|a@b.co|C|F",
        },
        "env": {"BASE_URL": "https://bank-demo.up.railway.app"},
        "specs": {},
    }
    written = agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        codegen,
        run_id="run-bank-1",
    )
    assert (tmp_path / "package.json").is_file()
    assert (tmp_path / "playwright.config.ts").is_file()
    assert (tmp_path / "fixtures" / "dynamic-auth.ts").is_file()
    assert (tmp_path / "fixtures" / "evidence.ts").is_file()
    assert (tmp_path / "fixtures" / "video-evidence-reporter.ts").is_file()
    assert not (tmp_path / "fixtures" / "flow-helpers.ts").exists() or "fixtures/flow-helpers.ts" not in written
    assert (tmp_path / "fixtures" / "bank-portal-auth.ts").is_file()
    assert (tmp_path / "fixtures" / "archivos" / "inscripciones-ach.txt").is_file()
    assert (tmp_path / "pages" / "InscripcionesPage.ts").is_file()
    assert (tmp_path / "pages" / "LoginPage.ts").is_file()
    assert (tmp_path / ".env").read_text(encoding="utf-8").startswith("APTP_RUN_ID=") or "BASE_URL=" in (tmp_path / ".env").read_text()
    assert not (tmp_path / "pages" / "BasePage.ts").exists()


def test_register_demo_secrets_fills_placeholder_values():
    store = RunSecretStore.for_run("run-placeholder-demo")
    store.register("login_user.cliente", "[REDACTED]", source="display")
    register_demo_secrets("run-placeholder-demo")
    assert store.get("login_user.cliente") == "312"


def test_sync_run_secrets_writes_login_user_env(tmp_path):
    project_dir = tmp_path / "bank-demo"
    project_dir.mkdir()
    (project_dir / "pages").mkdir()
    (project_dir / "pages" / "LoginPage.ts").write_text("export class LoginPage {}", encoding="utf-8")

    sync_run_secrets_to_project_env(
        project_dir,
        "run-env-sync",
        bank_portal=False,
        base_url="https://bank-portal.up.railway.app",
    )
    env_text = (project_dir / ".env").read_text(encoding="utf-8")
    assert "APTP_SECRET_LOGIN_USER_CLIENTE=312" in env_text
    assert "APTP_SECRET_LOGIN_USER_CLAVE=123456" in env_text


def test_register_demo_secrets_preserves_intake_values():
    store = RunSecretStore.for_run("run-overlay-demo")
    store.register("login_user.cliente", "900123456", source="intake")
    register_demo_secrets("run-overlay-demo")
    assert store.get("login_user.cliente") == "900123456"
    assert store.get("login_user.documento") == "20242587"


def test_register_demo_secrets():
    register_demo_secrets("run-demo-secrets")
    store = RunSecretStore.for_run("run-demo-secrets")
    assert store.get("login_user.cliente") == "312"
    assert store.get("login_user.token") == "63866019"


def test_bank_portal_always_scaffolds_login_page_without_codegen_hints(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    written = agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-login-scaffold",
    )
    login_path = tmp_path / "pages" / "LoginPage.ts"
    assert login_path.is_file(), "LoginPage.ts must be scaffolded for every bank-portal project"
    login_src = login_path.read_text(encoding="utf-8")
    assert "async login()" in login_src
    assert "getByTestId('login-page')" in login_src
    assert "__aptpDemo" in login_src
    assert "resetDemoData" in login_src
    assert "pages/LoginPage.ts" in written
    spec_files = list((tmp_path / "tests").glob("*.spec.ts"))
    assert spec_files
    assert "import { LoginPage }" in spec_files[0].read_text(encoding="utf-8")


def test_scaffold_missing_local_imports_heals_deleted_page(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-heal-scaffold",
    )
    login_path = tmp_path / "pages" / "LoginPage.ts"
    assert login_path.is_file()
    login_path.unlink()
    healed = agent._scaffold_missing_local_imports(tmp_path, [], bank_portal=True)
    assert healed is True
    assert login_path.is_file()
    assert "async login()" in login_path.read_text(encoding="utf-8")


def test_inscripciones_page_uses_get_by_testid(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-pom-scaffold",
    )
    insc_src = (tmp_path / "pages" / "InscripcionesPage.ts").read_text(encoding="utf-8")
    assert "getByTestId('crear-inscripcion-page')" in insc_src
    assert "locator('data-testid')" not in insc_src


def test_archivo_obligation_uses_plain_file_fixture():
    obl = {
        "id": "OBL-EMP-13331",
        "title": "EMPRESARIAL-13331: Crear inscripción archivo plano",
        "steps": ["Importar Archivo Plano", "Procesar archivo"],
        "automation_tier": "automated",
    }
    steps = map_obligation_to_playwright_steps(obl)
    upload = next(s for s in steps if s["action"] == "upload")
    assert upload["target"] == "crear-archivo-input"
    assert upload["data"] == PLAIN_FILE_FIXTURE
    assert any(s["target"] == "crear-procesar-archivo" for s in steps)


def test_bank_portal_spec_skips_inline_login(sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    spec = agent._generate_spec(
        scenario,
        scenario["page_object"],
        "crear-inscripcion-digitar",
        {},
        "https://bank-demo.up.railway.app",
        bank_portal=True,
    )
    assert "test.beforeEach" in spec
    assert spec.count("loginPage.login()") == 1
    assert "login(" not in spec.split("test('", 1)[-1].split("});", 1)[0]


def test_bank_portal_scaffolds_archivo_fixture_without_codegen(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-archivo-fixture",
    )
    fixture = tmp_path / "fixtures" / "archivos" / "inscripciones-ach.txt"
    assert fixture.is_file()
    assert "|" in fixture.read_text(encoding="utf-8")


def test_scaffold_missing_local_imports_heals_archivo_fixture(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-heal-fixture",
    )
    fixture = tmp_path / "fixtures" / "archivos" / "inscripciones-ach.txt"
    fixture.unlink()
    healed = agent._scaffold_missing_local_imports(tmp_path, [], bank_portal=True)
    assert healed is True
    assert fixture.is_file()


def test_manual_obligations_excluded_from_automatable():
    obls = [
        {"id": "OBL-1", "automation_tier": "manual", "title": "Revisión humana en oficina"},
        {"id": "OBL-2", "automation_tier": "automated"},
    ]
    auto = automatable_obligations(obls)
    assert len(auto) == 1
    assert auto[0]["id"] == "OBL-2"


def test_business_day_obligations_included_in_automatable():
    obls = [
        {
            "id": "OBL-EMP-13327",
            "title": "EMPRESARIAL-13327: Parámetro = 1 día Hábil",
            "automation_tier": "manual",
            "steps": ["Validar rechazo tras 1 día hábil"],
        },
        {"id": "OBL-2", "automation_tier": "automated", "title": "Crear inscripción"},
    ]
    auto = automatable_obligations(obls)
    assert len(auto) == 2


def test_business_day_obligation_steps_use_demo_api():
    obl = {
        "id": "OBL-EMP-13327",
        "title": "EMPRESARIAL-13327: Parámetro = 1 día Hábil",
        "steps": ["Enviada a otro banco sin respuesta"],
        "automation_tier": "automated",
    }
    steps = map_obligation_to_playwright_steps(obl)
    actions = [s["action"] for s in steps]
    assert "demo_evaluate" in actions
    assert any(s["target"] == "seedEnviadoOtroBanco" for s in steps)
    assert any(s["target"] == "triggerBusinessDayRejections" for s in steps)
    assert any(s["target"] == TEST_IDS["motivo_rechazo"] for s in steps)


def test_negative_login_scenario_maps_to_login_error():
    from src.automation.obligation_steps import map_functional_scenario_steps

    scenario = {
        "id": "OBL-1-invalid-password",
        "title": "Login con clave incorrecta",
        "scenario_type": "negative",
        "input_variation": "wrong_password",
    }
    steps = map_functional_scenario_steps(scenario)
    assert any(s["action"] == "login_negative" for s in steps)
    assert any(s["target"] == TEST_IDS["login_error"] for s in steps)


def test_no_session_skips_login_negative():
    from src.automation.obligation_steps import map_functional_scenario_steps

    scenario = {
        "id": "NEG-SESSION-NO-AUTH",
        "title": "Acceso a ruta protegida sin sesión",
        "scenario_type": "security",
        "input_variation": "no_session",
        "module": "session",
    }
    steps = map_functional_scenario_steps(scenario)
    assert steps[0]["action"] == "goto"
    assert steps[0]["data"] == ROUTES["login"]
    assert any(s["action"] == "clear_session" for s in steps)
    protected = next(s for s in steps if s["id"] == "step-01-goto-protected")
    assert protected["data"] == ROUTES["crear"]
    assert protected["data"] == "/inscripciones/crear"
    assert any(s["target"] == TEST_IDS["login_page"] for s in steps)
    assert not any(s["action"] == "login_negative" for s in steps)


def test_negative_no_token_client_uses_secondary_asset():
    from src.automation.obligation_steps import map_functional_scenario_steps
    from src.automation.bank_portal import bank_demo_no_token_secrets

    scenario = {
        "id": "OBL-1-no-token-client",
        "title": "Login cliente sin token secundario",
        "scenario_type": "negative",
        "input_variation": "no_token_client",
    }
    steps = map_functional_scenario_steps(scenario)
    assert any(s["action"] == "login_negative" and s["data"] == "no_token_client" for s in steps)
    no_token = bank_demo_no_token_secrets()
    assert no_token["no_token_client.cliente"] == "201319"
    assert no_token["no_token_client.documento"] == "1233896904"


def test_digitar_obligation_fills_detalle_form():
    obl = {
        "id": "OBL-EMP-13330",
        "title": "EMPRESARIAL-13330: Crear inscripción digitar",
        "steps": ["Ingresar Procesos de Inscripciones", "Seleccionar Digitar Inscripciones"],
        "expected_results": ["Visualiza proceso creado"],
        "automation_tier": "automated",
    }
    steps = map_obligation_to_playwright_steps(obl)
    actions = [s["action"] for s in steps]
    assert "login" in actions
    assert "testid_fill" in actions
    assert any(s["target"] == "crear-nombre-proceso" for s in steps)
    assert any(s["target"] == "crear-digitar" for s in steps)
    assert any(s["action"] == "fill_input_near_label" and "N° Identificación" in s["target"] for s in steps)
    assert any(s["action"] == "fill_input_near_label" and "N° Producto" in s["target"] for s in steps)
    assert any(s["target"] == "crear-continuar-detalle" for s in steps)


def test_validar_obligation_uses_num_proceso():
    obl = {
        "id": "OBL-EMP-13333",
        "title": "EMPRESARIAL-13333: Validar Inscripción",
        "steps": ["Validar proceso"],
        "automation_tier": "automated",
    }
    scenario = map_obligation_to_scenario(obl, "https://bank-demo.up.railway.app")
    steps = scenario["steps"]
    assert any(s["action"] == "seed_inscripcion" and s["target"] == "Creada" for s in steps)
    click = next(s for s in steps if s["action"] == "click_first_testid")
    assert "validar-" in click["target"]


def test_actualizar_obligation_uses_num_proceso():
    obl = {
        "id": "OBL-EMP-13334",
        "title": "EMPRESARIAL-13334: Actualizar Inscripción",
        "steps": ["Actualizar proceso enviado a otro banco"],
        "automation_tier": "automated",
    }
    scenario = map_obligation_to_scenario(obl, "https://bank-demo.up.railway.app")
    steps = scenario["steps"]
    assert any(s["action"] == "seed_inscripcion" and s["target"] == "Validada" for s in steps)
    click = next(s for s in steps if s["action"] == "click_first_testid")
    assert "actualizar-" in click["target"]


def test_pagos_obligation_selects_proceso():
    obl = {
        "id": "OBL-EMP-13335",
        "title": "EMPRESARIAL-13335: Pagar inscripción aceptada",
        "steps": ["Seleccionar inscripción aceptada", "Procesar pago"],
        "automation_tier": "automated",
    }
    scenario = map_obligation_to_scenario(obl, "https://bank-demo.up.railway.app")
    steps = scenario["steps"]
    assert any(s["action"] == "seed_inscripcion" and s["target"] == "Aceptada" for s in steps)
    assert any(s["target"] == "pagos-page" for s in steps)
    assert any(s["action"] == "select_option_by_text" for s in steps)
    assert any(s["target"] == "procesar-pago" for s in steps)


def test_transferencias_obligation_fills_valor():
    obl = {
        "id": "OBL-EMP-13336",
        "title": "EMPRESARIAL-13336: Realizar transferencia ACH",
        "steps": ["Ingresar valor", "Procesar transferencia"],
        "automation_tier": "automated",
    }
    scenario = map_obligation_to_scenario(obl, "https://bank-demo.up.railway.app")
    steps = scenario["steps"]
    assert any(s["target"] == "transferencias-page" for s in steps)
    assert any(s["action"] == "fill_input_near_label" and "Valor" in s["target"] for s in steps)
    assert any(s["action"] == "click_by_text" and "Procesar transferencia" in s["target"] for s in steps)


def test_generator_emits_demo_evaluate_for_business_days():
    obl = {
        "id": "OBL-EMP-13327",
        "title": "EMPRESARIAL-13327: Parámetro = 1 día Hábil",
        "automation_tier": "automated",
    }
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(obl, "https://bank-demo.up.railway.app")
    spec = agent._generate_spec(
        scenario,
        scenario["page_object"],
        "business-day-rejection",
        {},
        "https://bank-demo.up.railway.app",
        bank_portal=True,
    )
    assert "__aptpDemo" in spec
    assert "seedEnviadoOtroBanco" in spec
    assert "motivo-rechazo" in spec
    assert "filter-proceso').fill" in spec or 'filter-proceso").fill' in spec
    assert "HTMLInputElement.prototype" not in spec


def test_generator_bbva_destino_uses_unique_testid():
    from src.automation.obligation_steps import map_transfer_negative_steps

    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    steps = map_transfer_negative_steps("missing_enrollment")
    scenario = {
        "id": "NEG-TRANSFER-NO-ENROLL",
        "title": "Transferencia ACH sin inscripción asociada",
        "name": "Transferencia ACH sin inscripción asociada",
        "page_object": "ConsultasPage",
        "steps": steps,
        "assertions": [{"type": "visible", "expected": "transferencia-error"}],
        "tags": ["negative"],
    }
    spec = agent._generate_spec(
        scenario,
        "ConsultasPage",
        "transfer-missing-enrollment",
        {},
        "https://bank-demo.up.railway.app",
        bank_portal=True,
    )
    assert "transferencia-destino-bbva').first()" in spec or 'transferencia-destino-bbva").first()' in spec
    assert "getByRole('option', { name: /BBVA/i })" not in spec


def test_generator_no_session_clears_storage_and_uses_crear_route():
    from src.automation.obligation_steps import map_functional_scenario_steps

    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    steps = map_functional_scenario_steps(
        {
            "id": "NEG-SESSION-NO-AUTH",
            "title": "Acceso a ruta protegida sin sesión",
            "scenario_type": "security",
            "input_variation": "no_session",
            "module": "session",
        }
    )
    scenario = {
        "id": "NEG-SESSION-NO-AUTH",
        "title": "Acceso a ruta protegida sin sesión",
        "name": "Acceso a ruta protegida sin sesión",
        "page_object": "LoginPage",
        "steps": steps,
        "assertions": [{"type": "visible", "expected": "login-page"}],
        "tags": ["security"],
    }
    spec = agent._generate_spec(
        scenario,
        "LoginPage",
        "no-session",
        {},
        "https://bank-demo.up.railway.app",
        bank_portal=True,
    )
    assert "/inscripciones/crear" in spec
    assert "dav.session" in spec
    assert "login-page" in spec
    assert "goto('/crear')" not in spec
