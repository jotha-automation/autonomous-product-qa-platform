"""Tests for APTP test naming conventions."""
from src.intake.test_naming import (
    assign_case_codes,
    automation_tier_label_es,
    format_case_code,
    format_test_set_name,
    infer_test_set_type,
    merge_automation_tier,
)


def test_format_case_code():
    assert format_case_code(1, "EMPRESARIAL-13327: Parámetro = 1 día Hábil").startswith("CP_0001_")


def test_format_test_set_name():
    assert format_test_set_name("funcional", "portal-empresarial-59") == "TestSet:FUNCIONAL:portal-empresarial-59"


def test_assign_case_codes():
    rows = assign_case_codes([{"id": "OBL-1", "title": "Consulta saldos"}])
    assert rows[0]["cp_code"].startswith("CP_0001_")


def test_merge_automation_tier_manual_wins():
    assert merge_automation_tier("automated", "manual") == "manual"
    assert merge_automation_tier("partial", "automated") == "partial"


def test_infer_test_set_type_regresion():
    assert infer_test_set_type({"suite_section": "Regresión portal"}) == "REGRESION"


def test_automation_tier_label_es():
    assert automation_tier_label_es("manual") == "Operación manual"
