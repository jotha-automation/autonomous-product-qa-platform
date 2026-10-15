from src.agents.test_design import TestDesignAgent


def test_fallback_scenarios_from_requirements():
    agent = TestDesignAgent.__new__(TestDesignAgent)
    reqs = [
        {"id": "REQ-001", "description": "Hero shows full name"},
        {"id": "REQ-002", "description": "Contact form validates email"},
    ]
    scenarios = agent._fallback_scenarios(reqs)
    assert len(scenarios) > 2
    assert any(s.get("scenario_type") == "negative" for s in scenarios)
    happy = [s for s in scenarios if s.get("scenario_type") == "happy_path"]
    assert len(happy) == 2
