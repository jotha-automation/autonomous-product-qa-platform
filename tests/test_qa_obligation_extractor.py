"""QaObligationExtractor must preserve full obligation count from intake."""
from __future__ import annotations

import pytest

from src.agents.qa_obligation_extractor import QaObligationExtractorAgent
from src.intake.schemas.interpreted import DeliverableMeta, InterpretedIntake, TestObligation


def _sample_obligations(count: int) -> list[TestObligation]:
    rows: list[TestObligation] = []
    for i in range(1, count + 1):
        emp = 13320 + i
        rows.append(
            TestObligation(
                id=f"OBL-EMP-{emp}",
                title=f"EMPRESARIAL-{emp}: Caso de prueba {i}",
                steps=[f"Paso {j} para caso {i}" for j in range(1, 4)],
                expected_results=[f"Resultado esperado {i}"],
                automation_tier="partial",
            )
        )
    return rows


@pytest.mark.asyncio
async def test_refine_preserves_count_when_llm_groups(monkeypatch):
    agent = QaObligationExtractorAgent(memory=None)
    baseline = _sample_obligations(32)

    async def fake_ask_json(task, fallback, **kwargs):
        # Simulate LLM collapsing obligations into a small grouped set.
        grouped = fallback["obligations"][:6]
        return {"obligations": grouped}

    monkeypatch.setattr(agent, "ask_json", fake_ask_json)

    refined = await agent._refine_obligations(baseline, documents=[], context={})

    assert len(refined) == 32
    assert {o.id for o in refined} == {o.id for o in baseline}


@pytest.mark.asyncio
async def test_refine_applies_manual_tier_heuristic(monkeypatch):
    agent = QaObligationExtractorAgent(memory=None)
    baseline = [
        TestObligation(
            id="OBL-EMP-13327",
            title="EMPRESARIAL-13327: Parámetro = 1 día Hábil",
            steps=["Validar rechazo después de 1 día hábil"],
            expected_results=["Estado Rechazado"],
            automation_tier="partial",
        )
    ]

    async def fake_ask_json(task, fallback, **kwargs):
        return {"obligations": []}

    monkeypatch.setattr(agent, "ask_json", fake_ask_json)

    refined = await agent._refine_obligations(baseline, documents=[], context={})

    assert len(refined) == 1
    assert refined[0].automation_tier == "automated"


@pytest.mark.asyncio
async def test_execute_updates_deliverable_scope(monkeypatch):
    agent = QaObligationExtractorAgent(memory=None)
    obligations = _sample_obligations(32)
    interpreted = InterpretedIntake(
        obligations=obligations,
        deliverable_metadata=DeliverableMeta(scope="placeholder"),
    )

    async def fake_refine(obs, docs, context=None):
        return obs

    monkeypatch.setattr(agent, "_refine_obligations", fake_refine)
    agent.write_memory = lambda *args, **kwargs: None  # type: ignore[method-assign]

    result = await agent.execute({"interpreted_intake": interpreted.public_dict()})

    assert result["count"] == 32
    assert "32 obligaciones" in result["interpreted_intake"]["deliverable_metadata"]["scope"]
    assert "automatizables" in result["interpreted_intake"]["deliverable_metadata"]["scope"]
