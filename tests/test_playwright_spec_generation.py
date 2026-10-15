from src.agents.playwright_generator import PlaywrightGeneratorAgent


import json

import pytest

from src.agents.playwright_generator import PlaywrightGeneratorAgent


@pytest.mark.asyncio
async def test_request_codegen_json_does_not_raise_on_task_prompt(monkeypatch):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    monkeypatch.setattr(
        "src.agents.playwright_generator.output_tokens",
        lambda agent_name, **kwargs: 1024,
    )

    async def fake_ask_llm(prompt, **kwargs):
        assert "hello" in prompt
        return json.dumps({"pages": {}, "specs": {}})

    agent.ask_llm = fake_ask_llm
    agent.parse_json_response = PlaywrightGeneratorAgent.parse_json_response.__get__(agent)

    result = await agent._request_codegen_json("hello codegen task")
    assert result["pages"] == {}
    assert result["specs"] == {}


@pytest.mark.asyncio
async def test_request_codegen_json_falls_back_on_rate_limit(monkeypatch):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    monkeypatch.setattr(
        "src.agents.playwright_generator.output_tokens",
        lambda agent_name, **kwargs: 1024,
    )

    async def fake_ask_llm(prompt, **kwargs):
        raise Exception(
            "Azure OpenAI API call failed: Error code: 429 - "
            "{'error': {'code': 'RateLimitReached', 'message': 'rate limit'}}"
        )

    agent.ask_llm = fake_ask_llm
    agent.parse_json_response = PlaywrightGeneratorAgent.parse_json_response.__get__(agent)

    result = await agent._request_codegen_json("hello codegen task")

    assert result["template_only"] is True
    assert result["llm_unavailable"] == "rate_limited"
    assert result["specs"] == {}


def test_js_regex_literal_escapes_forward_slashes():
    lit = PlaywrightGeneratorAgent._js_regex_literal("/inscripciones/crear")
    assert lit == "/\\/inscripciones\\/crear/"
    # Must not produce the malformed `//...` form that breaks the JS parser.
    assert not lit.startswith("//")


def test_js_regex_literal_empty_defaults_to_wildcard():
    assert PlaywrightGeneratorAgent._js_regex_literal("") == "/.+/"


def test_js_regex_literal_does_not_double_escape():
    lit = PlaywrightGeneratorAgent._js_regex_literal("\\/already")
    assert lit == "/\\/already/"


def test_url_assertion_emits_valid_regex():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    lines = agent._steps_to_spec_ts(
        {
            "steps": [{"action": "goto", "target": "/inscripciones/crear"}],
            "assertions": [{"type": "url", "expected": "/inscripciones/crear"}],
        },
        "InscripcionesPage",
        "https://example.com",
    )
    url_line = next(line for line in lines if "toHaveURL" in line)
    assert "toHaveURL(//" not in url_line
    assert "toHaveURL(/\\/inscripciones\\/crear/)" in url_line


def test_repair_invalid_url_regex_fixes_double_slash():
    broken = "await expect(page).toHaveURL(//inscripciones/crear/);"
    fixed = PlaywrightGeneratorAgent._repair_invalid_url_regex(broken)
    assert "toHaveURL(//" not in fixed
    assert "toHaveURL(/\\/inscripciones\\/crear/)" in fixed


def test_repair_invalid_url_regex_leaves_valid_regex_untouched():
    valid = "await expect(page).toHaveURL(/.+/);"
    assert PlaywrightGeneratorAgent._repair_invalid_url_regex(valid) == valid


def test_deterministic_ts_fixes_repairs_spec(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    spec = tmp_path / "broken.spec.ts"
    spec.write_text(
        "import { test, expect } from '@playwright/test';\n"
        "test('x', async ({ page }) => {\n"
        "  await expect(page).toHaveURL(//inscripciones/crear/);\n"
        "});\n",
        encoding="utf-8",
    )
    changes = agent._deterministic_ts_fixes([spec])
    assert len(changes) == 1
    assert changes[0]["strategy"] == "deterministic_regex_fix"
    assert "toHaveURL(//" not in spec.read_text(encoding="utf-8")


def test_failing_ts_files_matches_by_path_with_spaces(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    spec = tests_dir / "a.spec.ts"
    spec.write_text("test", encoding="utf-8")
    other = tests_dir / "b.spec.ts"
    other.write_text("test", encoding="utf-8")
    stderr = f"SyntaxError: {spec}: Unexpected token (3:40)"
    failing = agent._failing_ts_files(stderr, tmp_path)
    assert spec in failing
    assert other not in failing


def test_generate_spec_does_not_break_test_signature_with_tags():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    spec = agent._generate_spec(
        {
            "id": "FS-009",
            "name": "Contact form validates input and submits successfully",
            "tags": ["smoke", "regression"],
        },
        "ContactSection",
        "contact-form-validates-input",
        {},
        "https://example.com",
    )
    assert "async ({ page }, testInfo) =>" in spec
    assert "// tags: smoke, regression" in spec
    assert ", // tags:" not in spec
    assert "test('FS-009:" in spec


def test_slim_scenarios_preserves_full_steps():
    scenarios = [{"id": "TC-1", "name": "A", "steps": [{"action": "click"}] * 20}]
    slim = PlaywrightGeneratorAgent._slim_scenarios_for_codegen(scenarios)
    assert len(slim[0]["steps"]) == 20


def test_slug_for_spec_strips_commas_and_special_chars():
    slug = PlaywrightGeneratorAgent._slug_for_spec(
        "Direct access to LinkedIn, GitHub, and email via clickable icons in footer"
    )
    assert "," not in slug
    assert slug.startswith("direct-access")


def test_generate_spec_filters_duplicate_playwright_imports():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    spec = agent._generate_spec(
        {"id": "TC-1", "name": "Contact form validates", "tags": ["regression"]},
        "ContactSection",
        "contact-form-validates",
        {
            "extra_imports": [
                "import { test, expect } from '@playwright/test';",
                "import { ContactSection } from '../pages/ContactSection';",
            ],
            "test_body_lines": ["await page.goto('/');"],
            "timeout_ms": 15000,
        },
        "https://example.com",
    )
    assert spec.count("import { test, expect } from '@playwright/test';") == 1
    assert spec.count("import { ContactSection }") == 1


def test_generate_project_removes_stale_spec_files(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    project_dir = tmp_path / "demo"
    tests_dir = project_dir / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "old-stale-test.spec.ts").write_text("// stale", encoding="utf-8")

    written = agent._generate_project(
        project_dir,
        {
            "scenarios": [
                {"id": "TC-1", "name": "Hero section loads", "page_object": "HeroSection", "steps": []},
            ]
        },
        "demo",
        "https://example.com",
        {"pages": {}, "specs": {}},
        run_id="run-test",
    )

    assert not (tests_dir / "old-stale-test.spec.ts").exists()
    assert any("hero-section-loads.spec.ts" in path for path in written)


def test_normalize_extra_imports_bank_portal_paths():
    """Regression: 6 LLM path hints must not duplicate captureStepEvidence."""
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    bank_extra_imports = [
        "../fixtures/evidence",
        "../fixtures/bank-portal-auth",
        "../fixtures/test-data",
        "../fixtures/flow-helpers",
        "../pages/LoginPage",
        "../pages/InscripcionesPage",
    ]
    spec = agent._generate_spec(
        {
            "id": "OBL-EMP-13330",
            "name": "Crear inscripción digitar",
            "tags": ["EMPRESARIAL-13330"],
            "aut_profile": "bank_portal",
        },
        "InscripcionesPage",
        "crear-inscripcion-digitar",
        {
            "extra_imports": bank_extra_imports,
            "test_body_lines": [
                "await page.getByTestId('crear-digitar').click();",
                "await captureStepEvidence(page, testInfo, 'step-1', 'digitar');",
            ],
            "before_each_lines": ["await login();"],
        },
        "https://bank-portal.up.railway.app",
        bank_portal=True,
    )
    assert spec.count("import { captureStepEvidence } from '../fixtures/evidence';") == 1
    assert spec.count("import { captureStepEvidence") == 1
    assert "loginBankPortal" in spec
    assert "validManualData" in spec
    assert "import * as flow_helpers" in spec
    assert "const loginPage = new LoginPage(page)" in spec
    assert "await loginPage.login()" in spec
    assert "await login();" not in spec


def test_dedupe_evidence_imports_strips_duplicates():
    block = (
        "import { captureStepEvidence } from '../fixtures/evidence';\n"
        "import { captureStepEvidence, loginBankPortal } from '../fixtures/bank-portal-auth';"
    )
    deduped = PlaywrightGeneratorAgent._dedupe_evidence_imports(block)
    assert deduped.count("captureStepEvidence") == 1
    assert "loginBankPortal" in deduped


def test_generate_spec_normalizes_extra_imports_path():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    spec = agent._generate_spec(
        {"id": "TC-1", "name": "Smoke", "tags": ["smoke"]},
        "HomePage",
        "smoke-home",
        {
            "extra_imports": ["../fixtures/evidence"],
            "test_body_lines": [
                "const homePage = new HomePage(page);",
                "await homePage.goto();",
            ],
            "timeout_ms": 15000,
        },
        "https://example.com",
    )
    assert "../fixtures/evidence\n" not in spec
    assert "import { captureStepEvidence } from '../fixtures/evidence';" in spec
    assert "const pom = new HomePage" not in spec
    assert "const homePage = new HomePage" in spec


def test_normalize_page_methods_strips_invalid_llm_names():
    methods = [
        {"name": "constructor", "body": "this.page = page;"},
        {"name": "gotoLogin()()", "body": "await this.page.goto('/login');"},
        {"name": "submit()", "body": "await this.page.click('button');"},
    ]
    normalized = PlaywrightGeneratorAgent._normalize_page_methods(methods)
    assert [m["name"] for m in normalized] == ["gotoLogin", "submit"]


def test_generate_page_rejects_async_constructor_hallucination():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    page = agent._generate_page(
        {"locators": {"primary": "body"}, "steps": []},
        "InscripcionesPage",
        "https://example.com",
        {
            "methods": [
                {"name": "constructor", "body": "this.page = page;"},
                {"name": "gotoLogin()()", "body": "await this.page.goto('/login');"},
            ]
        },
    )
    assert "async constructor" not in page
    assert "gotoLogin()()" not in page
    assert "async gotoLogin()" in page


def test_sanitize_method_body_strips_nested_async_wrapper():
    body = PlaywrightGeneratorAgent._sanitize_method_body(
        "gotoCrear",
        "async gotoCrear() { await this.page.goto('/inscripciones/crear'); }",
    )
    assert body == "await this.page.goto('/inscripciones/crear');"


def test_generate_page_rejects_duplicate_nested_methods():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    page = agent._generate_page(
        {"locators": {"primary": "body"}, "steps": []},
        "InscripcionesPage",
        "https://example.com",
        {
            "methods": [
                {
                    "name": "gotoCrear",
                    "body": "async gotoCrear() { await this.page.goto('/inscripciones/crear'); }",
                }
            ]
        },
    )
    assert page.count("async gotoCrear()") == 1
    assert "await this.page.goto('/inscripciones/crear');" in page


def test_origin_base_url_strips_path():
    assert PlaywrightGeneratorAgent._origin_base_url("https://bank-demo.up.railway.app/login") == (
        "https://bank-demo.up.railway.app"
    )


def test_normalize_extra_imports_bank_portal_paths():
    """Paths from run 8ac996766521 — must not map every fixture to captureStepEvidence."""
    paths = [
        "../fixtures/bank-portal-auth",
        "../fixtures/flow-helpers",
        "../fixtures/test-data",
        "../pages/LoginPage",
        "../fixtures/evidence",
        "fixtures/test-data.ts",
    ]
    lines = PlaywrightGeneratorAgent._normalize_extra_imports(paths, page_class="InscripcionesPage")
    block = "\n".join(lines)
    assert "bank-portal-auth" not in block
    assert "validManualData" in block
    assert "validArchivoPlano" in block
    assert "invalidManualData" in block
    assert "flow_helpers" in block
    assert "LoginPage" in block
    assert "import { captureStepEvidence } from '../fixtures/evidence';" in block
    assert block.count("captureStepEvidence") == 1


def test_generate_spec_rewrites_before_each_login():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    spec = agent._generate_spec(
        {
            "id": "OBL-EMP-13330",
            "name": "Crear inscripción manual",
            "tags": ["EMPRESARIAL-13330"],
            "steps": [{"action": "testid_click", "target": "crear-digitar"}],
        },
        "InscripcionesPage",
        "crear-inscripcion-manual",
        {"before_each_lines": ["await login();"]},
        "https://bank-demo.up.railway.app",
        bank_portal=True,
    )
    assert "await login();" not in spec
    assert "await loginPage.login();" in spec
    assert "import { LoginPage }" in spec


def test_boilerplate_templates_are_minimal_only():
    from pathlib import Path

    from src.agents.playwright_generator import TEMPLATES_DIR

    allowed = {
        "README.md",
        "package.json.j2",
        "playwright.config.ts.j2",
        "fixtures/dynamic-auth.ts",
        "fixtures/evidence.ts",
        "fixtures/video-evidence-reporter.ts",
        "pages/LoginPage.ts",
        "pages/InscripcionesPage.ts",
        "pages/ConsultasPage.ts",
    }
    found = {
        str(p.relative_to(TEMPLATES_DIR)).replace("\\", "/")
        for p in TEMPLATES_DIR.rglob("*")
        if p.is_file()
    }
    assert found == allowed


def test_generate_project_loads_env_in_playwright_config(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    agent._generate_project(
        tmp_path,
        {"scenarios": [{"id": "TC-1", "name": "Smoke", "page_object": "HomePage", "steps": []}]},
        "demo",
        "https://bank-demo.up.railway.app/login",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="",
    )
    config = (tmp_path / "playwright.config.ts").read_text(encoding="utf-8")
    assert "readFileSync" in config
    assert "video-evidence-reporter" in config
    assert "video: 'on'" in config
    assert "BASE_URL=https://bank-demo.up.railway.app" in (tmp_path / ".env").read_text(encoding="utf-8")
