"""Tests for live Playwright explorer helpers."""
from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.automation.obligation_steps import map_obligation_to_scenario
from src.services.playwright_explorer import (
    _best_testid_match,
    _testid_from_target,
    build_codegen_from_exploration,
    merge_explored_scenarios,
    select_scenarios_for_exploration,
)


def test_testid_from_target_variants():
    assert _testid_from_target("crear-digitar") == "crear-digitar"
    assert _testid_from_target("[data-testid=login-page]") == "login-page"
    assert _testid_from_target("testid:login-submit") == "login-submit"


def test_best_testid_match_fuzzy():
    available = ["crear-continuar-header", "crear-digitar", "login-page"]
    assert _best_testid_match("crear_digitar", available) == "crear-digitar"
    assert _best_testid_match("continuar-header", available) == "crear-continuar-header"


def test_build_codegen_from_exploration_bank_scenario():
    scenario = map_obligation_to_scenario(
        {
            "id": "OBL-EMP-13330",
            "external_id": "EMPRESARIAL-13330",
            "title": "Crear inscripción digitar",
            "steps": ["Digitar"],
            "automation_tier": "automated",
        },
        "https://bank-demo.up.railway.app",
    )
    scenario["aut_profile"] = "bank_portal"
    out = build_codegen_from_exploration({"scenarios": [scenario]}, {"scenarios": [scenario]})
    assert out["from_exploration"] is True
    page = out["pages"].get("InscripcionesPage") or out["pages"].get(scenario["page_object"])
    assert page is not None
    spec = out["specs"].get("OBL-EMP-13330", {})
    body = "\n".join(spec.get("test_body_lines") or [])
    assert "crear-inscripcion-page" in body or "validar-page" in body
    assert "crear-nombre-proceso" in body or "validar-page" in body


def test_select_scenarios_for_exploration_caps_and_dedupes_modules():
    scenarios = []
    for i in range(30):
        module = ["crear", "validar", "consultas", "pagos"][i % 4]
        scenarios.append(
            {
                "id": f"OBL-{i}",
                "name": f"{module}-case-{i}",
                "tags": [module, "bank-portal"],
            }
        )
    chosen = select_scenarios_for_exploration(scenarios)
    assert len(chosen) <= 12
    assert len(chosen) >= 4
    merged = merge_explored_scenarios(scenarios, chosen)
    assert len(merged) == 30
    assert merged[0]["id"] == "OBL-0"


def test_protected_fixtures_not_overwritten_by_llm(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    written: list[str] = []
    agent._scaffold_minimal_boilerplate(tmp_path, "demo", "https://example.com", "")
    original = (tmp_path / "fixtures" / "evidence.ts").read_text(encoding="utf-8")
    agent._write_llm_env_and_fixtures(
        tmp_path,
        {
            "fixtures": {
                "evidence.ts": "// tampered",
                "dynamic-auth.ts": "// tampered",
                "video-evidence-reporter.ts": "// tampered",
                "custom.ts": "export const x = 1;",
            }
        },
        written,
    )
    assert (tmp_path / "fixtures" / "evidence.ts").read_text(encoding="utf-8") == original
    assert (tmp_path / "fixtures" / "custom.ts").read_text(encoding="utf-8") == "export const x = 1;"
