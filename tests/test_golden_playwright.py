"""Golden INC1353166 Playwright template — build and materialize."""
from pathlib import Path

import pytest

from src.automation.golden_playwright import (
    GOLDEN_DIR,
    _BUNDLED_SPEC_FIXTURE,
    _DEV_SPEC_FIXTURE,
    build_inc1353166_test_plan,
    golden_project_available,
    inc1353166_spec_path,
    load_golden_test_plan,
    load_inc1353166_obligations,
    materialize_golden_playwright,
    use_golden_playwright,
)


def test_load_inc1353166_has_32_obligations():
    obligations = load_inc1353166_obligations()
    assert len(obligations) == 32
    ids = {o.get("external_id") for o in obligations}
    assert "EMPRESARIAL-13327" in ids
    assert "EMPRESARIAL-13365" in ids


def test_build_test_plan_includes_negatives_and_cp_codes():
    plan = build_inc1353166_test_plan("https://bank-portal.up.railway.app")
    scenarios = plan.get("scenarios") or []
    assert len(scenarios) >= 100
    cp_codes = [s.get("cp_code") for s in scenarios if str(s.get("cp_code", "")).startswith("CP_")]
    assert len(cp_codes) >= 32
    login_neg = [s for s in scenarios if str(s.get("id", "")).startswith("NEG-LOGIN")]
    assert len(login_neg) == 3
    variations = {
        str(s.get("input_variation") or "")
        for s in scenarios
        if s.get("scenario_type") in {"negative", "boundary", "resilience", "security"}
    }
    assert "malformed_input" in variations
    assert "empty_required" in variations
    assert "invalid_format" in variations
    assert "amount_zero" in variations
    assert any(
        s.get("scenario_type") in {"negative", "resilience", "boundary", "security"}
        for s in scenarios
    )


def test_inc1353166_spec_path_uses_bundled_fixture_when_tests_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("APTP_INC1353166_SPEC_PATH", raising=False)
    missing_dev = tmp_path / "missing" / "inc1353166-testlink.txt"
    monkeypatch.setattr("src.automation.golden_playwright._DEV_SPEC_FIXTURE", missing_dev)
    assert inc1353166_spec_path() == _BUNDLED_SPEC_FIXTURE
    assert _BUNDLED_SPEC_FIXTURE.is_file()


def test_load_golden_test_plan_prefers_cached_plan(tmp_path):
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    cached = {"target_url": "https://example.test", "scenarios": [{"id": "cached"}], "suite": {}}
    (golden_dir / "golden-test-plan.json").write_text(
        __import__("json").dumps(cached),
        encoding="utf-8",
    )
    plan = load_golden_test_plan("https://bank-portal.up.railway.app", golden_dir=golden_dir)
    assert plan["target_url"] == "https://bank-portal.up.railway.app"
    assert plan["scenarios"][0]["id"] == "cached"


def test_use_golden_playwright_default_true(monkeypatch):
    monkeypatch.delenv("APTP_USE_GOLDEN_PLAYWRIGHT", raising=False)
    assert use_golden_playwright() is True
    monkeypatch.setenv("APTP_USE_GOLDEN_PLAYWRIGHT", "false")
    assert use_golden_playwright() is False


def test_use_golden_only_defaults_with_golden_playwright(monkeypatch):
    from src.automation.golden_playwright import use_golden_only

    monkeypatch.delenv("APTP_GOLDEN_ONLY", raising=False)
    monkeypatch.delenv("APTP_USE_GOLDEN_PLAYWRIGHT", raising=False)
    assert use_golden_only() is True
    monkeypatch.setenv("APTP_USE_GOLDEN_PLAYWRIGHT", "false")
    assert use_golden_only() is False
    monkeypatch.setenv("APTP_USE_GOLDEN_PLAYWRIGHT", "true")
    monkeypatch.setenv("APTP_GOLDEN_ONLY", "false")
    assert use_golden_only() is False


def test_build_golden_project(tmp_path):
    golden_dir = tmp_path / "golden"
    from src.automation.golden_playwright import build_golden_project

    written, plan = build_golden_project(golden_dir, target_url="https://bank-portal.up.railway.app")
    assert (golden_dir / "tests").is_dir()
    assert any(golden_dir.glob("tests/*.spec.ts"))
    assert (golden_dir / "golden-manifest.json").is_file()
    assert len(written) > 10
    assert plan.get("obligations_count") == 32


def test_materialize_golden_playwright(tmp_path, monkeypatch):
    golden_dir = tmp_path / "golden-src"
    from src.automation.golden_playwright import build_golden_project

    build_golden_project(golden_dir, target_url="https://bank-portal.up.railway.app")
    monkeypatch.setattr("src.automation.golden_playwright.GOLDEN_DIR", golden_dir)
    monkeypatch.setattr("src.automation.golden_playwright.golden_project_available", lambda: True)

    dest = tmp_path / "project"
    written, plan = materialize_golden_playwright(
        dest,
        "DEMO-TEST",
        "https://bank-portal.up.railway.app",
        run_id="run-golden-test",
    )
    assert dest.is_dir()
    assert (dest / "playwright.config.ts").is_file()
    assert (dest / ".env").is_file()
    assert any(dest.glob("tests/*.spec.ts"))
    assert len(written) > 5
    assert len(plan.get("scenarios") or []) >= 32


@pytest.mark.skipif(not golden_project_available(), reason="Golden template not built in repo")
def test_repo_golden_template_present():
    assert GOLDEN_DIR.is_dir()
    manifest = GOLDEN_DIR / "golden-manifest.json"
    assert manifest.is_file()
    assert (GOLDEN_DIR / "golden-test-plan.json").is_file()
