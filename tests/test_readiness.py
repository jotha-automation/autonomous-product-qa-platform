import pytest
from src.agents.readiness import ReadinessAgent
from src.intake.schemas.canonical import DocumentTypeEnum


@pytest.mark.asyncio
async def test_readiness_ready_when_all_types_present():
    agent = ReadinessAgent()
    documents = [
        {"document_type": DocumentTypeEnum.BRD.value},
        {"document_type": DocumentTypeEnum.PRD.value},
        {"document_type": DocumentTypeEnum.UC.value},
        {"document_type": DocumentTypeEnum.AC.value},
    ]
    result = await agent.execute({"documents": documents, "interpreted_intake": None})
    assert result["status"] == "READY"
    assert result["missing_artifacts"] == []


@pytest.mark.asyncio
async def test_readiness_warn_when_ac_missing():
    agent = ReadinessAgent()
    documents = [
        {"document_type": "BRD"},
        {"document_type": "PRD"},
        {"document_type": "UC"},
    ]
    result = await agent.execute({"documents": documents, "interpreted_intake": None})
    assert result["status"] == "WARN"
    assert "AC" in result["missing_artifacts"]
    assert result.get("block_run") is False
