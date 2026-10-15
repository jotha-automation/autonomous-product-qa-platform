"""Guardrails for Playwright npm / Docker / Python version alignment."""
import json
from pathlib import Path

import pytest

from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.automation.obligation_steps import map_obligation_to_scenario
from src.playwright_version import PLAYWRIGHT_DOCKER_IMAGE, PLAYWRIGHT_NPM_SPEC, PLAYWRIGHT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_playwright_version_constant_is_exact_pin():
    assert PLAYWRIGHT_NPM_SPEC == PLAYWRIGHT_VERSION
    assert "^" not in PLAYWRIGHT_NPM_SPEC
    assert PLAYWRIGHT_DOCKER_IMAGE == f"mcr.microsoft.com/playwright:v{PLAYWRIGHT_VERSION}-jammy"


def test_package_json_template_pins_exact_playwright_version():
    template = (REPO_ROOT / "src/agents/templates/playwright/package.json.j2").read_text(encoding="utf-8")
    assert '"@playwright/test": "{{playwright_version}}"' in template
    playwright_line = next(line for line in template.splitlines() if "@playwright/test" in line)
    assert "^" not in playwright_line


def test_dockerfile_matches_playwright_docker_image():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG PLAYWRIGHT_VERSION={PLAYWRIGHT_VERSION}" in dockerfile
    assert "FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-jammy" in dockerfile


def test_requirements_txt_matches_playwright_version():
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "playwright>=1.61.0,<1.62.0" in requirements


def test_generator_writes_exact_playwright_version(tmp_path, sample_obligation):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    scenario = map_obligation_to_scenario(sample_obligation, "https://bank-demo.up.railway.app")
    agent._generate_project(
        tmp_path,
        {"scenarios": [scenario], "aut_profile": "bank_portal", "suite": {"aut_profile": "bank_portal"}},
        "bank-demo",
        "https://bank-demo.up.railway.app",
        {"pages": {}, "specs": {}, "fixtures": {}, "env": {}},
        run_id="run-version-pin",
    )
    pkg = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert pkg["devDependencies"]["@playwright/test"] == PLAYWRIGHT_NPM_SPEC


def test_skip_playwright_install_when_browsers_path_exists(monkeypatch, tmp_path):
    browsers_dir = tmp_path / "ms-playwright"
    shell = browsers_dir / "chromium_headless_shell-1228" / "chrome-linux" / "headless_shell"
    shell.parent.mkdir(parents=True)
    shell.write_text("", encoding="utf-8")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_dir))
    monkeypatch.delenv("APTP_SKIP_PLAYWRIGHT_INSTALL", raising=False)
    assert PlaywrightGeneratorAgent._browsers_available() is True


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
