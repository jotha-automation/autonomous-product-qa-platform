"""Full obligation → Playwright scenario coverage."""
from src.automation.scenario_coverage import (
    build_scenarios_from_obligations,
    merge_scenario_coverage,
)


def test_build_scenarios_one_per_obligation():
    obligations = [
        {"id": f"OBL-{i:03d}", "title": f"Case {i}", "automation_tier": "partial", "steps": ["step"]}
        for i in range(1, 33)
    ]
    scenarios = build_scenarios_from_obligations(obligations, "https://example.com", bank_portal=False)
    assert len(scenarios) == 32
    assert {s["id"] for s in scenarios} == {o["id"] for o in obligations}


def test_manual_obligations_included_with_skip_flag():
    obligations = [
        {"id": "OBL-001", "title": "Manual calendar", "automation_tier": "manual", "steps": ["wait 1 día hábil"]},
        {"id": "OBL-002", "title": "UI flow", "automation_tier": "automated", "steps": ["login"]},
    ]
    scenarios = build_scenarios_from_obligations(obligations, "https://bank.test", bank_portal=True)
    assert len(scenarios) == 2
    manual = next(s for s in scenarios if s["id"] == "OBL-001")
    assert manual.get("manual") is True


def test_merge_preserves_all_baseline_scenarios():
    baseline = [{"id": "A", "name": "a"}, {"id": "B", "name": "b"}]
    llm = [{"id": "A", "name": "a-refined", "steps": [{"action": "goto"}]}]
    merged = merge_scenario_coverage(baseline, llm)
    assert len(merged) == 2
    assert merged[0]["name"] == "a-refined"
    assert merged[0]["steps"]
