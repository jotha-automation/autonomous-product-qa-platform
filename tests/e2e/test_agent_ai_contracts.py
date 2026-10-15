"""Verify mock LLM responses satisfy agent JSON contracts (no pipeline failures)."""
from __future__ import annotations

import json

import pytest

from src.services.mock_llm import mock_response


AGENT_CONTRACTS = [
    ("ReadinessAgent", {"status": str}),
    ("RequirementsAnalystAgent", {"requirements": list}),
    ("CoverageAgent", {"coverage_score": (int, float)}),
    ("TestDesignAgent", {"functional_scenarios": list}),
    ("TraceabilityAgent", {"matrix": list}),
    ("ClientIntakeInterpreterAgent", {"obligations": list}),
    ("QaObligationExtractorAgent", {"obligations": list}),
    ("PlaywrightArchitectAgent", {"scenarios": list}),
    ("PlaywrightGeneratorAgent", {"specs": dict}),
    ("DefectDetectionAgent", {"release_readiness": str}),
    ("IntakeInterpretationAgent", {"documents": list}),
]


def _assert_shape(data: dict, shape: dict) -> None:
    for key, expected_type in shape.items():
        assert key in data, f"Missing key {key} in {data}"
        if isinstance(expected_type, tuple):
            assert isinstance(data[key], expected_type), f"{key} wrong type"
        else:
            assert isinstance(data[key], expected_type), f"{key} wrong type"


@pytest.mark.parametrize("agent_name,shape", AGENT_CONTRACTS)
def test_mock_llm_agent_contract(agent_name, shape):
    raw = mock_response(agent_name, f"Sample prompt for {agent_name} with BRD PRD UC AC acceptance")
    data = json.loads(raw)
    _assert_shape(data, shape)


def test_mock_llm_client_intake_obligations_have_ids():
    raw = mock_response("ClientIntakeInterpreterAgent", "extract obligations EMPRESARIAL-13327 pasos")
    data = json.loads(raw)
    for obl in data["obligations"]:
        assert obl.get("id")
        assert obl.get("title")
        assert obl.get("automation_tier") in {"automated", "manual", "partial", None}
