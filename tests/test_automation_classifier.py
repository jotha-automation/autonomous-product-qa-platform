"""Tests for obligation automation classification."""
from src.automation.automation_classifier import (
    apply_automation_classification,
    classify_automation_tier,
    partition_obligations,
    reconcile_automation_classification,
)


def test_manual_for_biometrics():
    obl = {
        "title": "Validar enrolamiento con huella dactilar",
        "steps": ["Usuario presenta biometría en dispositivo"],
    }
    tier, blockers, _ = classify_automation_tier(obl)
    assert tier == "manual"
    assert "biometrics_required" in blockers


def test_manual_for_atm_and_dataphone():
    atm = {"title": "Retiro en cajero automático", "steps": ["Insertar tarjeta en ATM"]}
    tier, blockers, _ = classify_automation_tier(atm)
    assert tier == "manual"
    assert "atm_physical_device" in blockers

    pos = {"title": "Pago con datáfono en comercio", "steps": ["Deslizar tarjeta en POS"]}
    tier2, blockers2, _ = classify_automation_tier(pos)
    assert tier2 == "manual"
    assert "payment_terminal" in blockers2


def test_manual_for_time_window():
    obl = {
        "title": "Proceso solo entre las 8:00 y 17:00 en días hábiles",
        "steps": ["Ejecutar fuera de horario de atención"],
    }
    tier, blockers, _ = classify_automation_tier(obl)
    assert tier == "manual"
    assert "time_window" in blockers


def test_automated_bank_portal_business_day():
    obl = {
        "title": "EMPRESARIAL-13327: Parámetro = 1 día Hábil",
        "steps": ["Login portal", "Configurar parámetro", "Validar rechazo"],
    }
    tier, blockers, rationale = classify_automation_tier(obl, bank_portal=True)
    assert tier == "automated"
    assert not blockers
    assert "demo" in rationale.casefold()


def test_reconcile_keeps_manual_over_llm_automated():
    obl = {"id": "1", "title": "Pago con datáfono en comercio", "steps": ["Deslizar tarjeta"]}
    merged = reconcile_automation_classification(obl, {"automation_tier": "automated"}, bank_portal=False)
    assert merged["automation_tier"] == "manual"


def test_partition_obligations():
    obligations = [
        {"id": "1", "title": "Login portal", "steps": ["A", "B"]},
        {"id": "2", "title": "Retiro ATM", "steps": ["Ir al cajero"]},
    ]
    result = partition_obligations(obligations, bank_portal=True)
    assert result["manual_count"] == 1
    assert result["automatable_count"] == 1
    classified = apply_automation_classification(obligations[0], bank_portal=True)
    assert classified["automation_tier"] in {"automated", "partial"}
