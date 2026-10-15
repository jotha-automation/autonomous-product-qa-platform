"""PDF-first intake: single TestLink PDF → virtual docs + obligations."""
from pathlib import Path

import pytest

from src.agents.readiness import ReadinessAgent
from src.intake.normalization import normalize_intake_documents
from src.memory.enhanced_memory import get_memory

FIXTURE = Path(__file__).parent / "fixtures" / "documents" / "inc1353166-testlink.txt"


@pytest.fixture
def testlink_pdf_doc():
    if not FIXTURE.is_file():
        pytest.skip("INC1353166 fixture missing")
    return {
        "id": "inc1353166",
        "filename": "pruebas-inc1353166.pdf",
        "document_type": "OTHER",
        "raw_text": FIXTURE.read_text(encoding="utf-8", errors="replace"),
    }


@pytest.mark.asyncio
async def test_normalize_single_testlink_pdf(testlink_pdf_doc):
    normalized, meta = await normalize_intake_documents([testlink_pdf_doc])
    assert meta["ai_invoked"] is True
    assert "content_interpretation" in meta["method"]
    types = {d["document_type"] for d in normalized}
    assert types == {"OTHER"}
    interpreted = meta.get("interpreted_intake") or {}
    obligations = interpreted.get("obligations") or []
    assert len(obligations) >= 10
    assert meta.get("chunk_count", 0) >= 10


@pytest.mark.asyncio
async def test_readiness_pdf_first_without_url_warns_not_blocks_on_types(testlink_pdf_doc):
    normalized, meta = await normalize_intake_documents([testlink_pdf_doc])
    agent = ReadinessAgent(get_memory())
    result = await agent.execute(
        {
            "documents": normalized,
            "interpreted_intake": meta.get("interpreted_intake"),
            "metadata": {},
        }
    )
    assert result["status"] in {"WARN", "READY"}
    assert "BRD" not in (result.get("gaps") or [])
    assert result.get("obligation_count", 0) >= 10
    assert result.get("block_run") is True
    assert "target_url" in result.get("gaps", [])


@pytest.mark.asyncio
async def test_readiness_pdf_first_with_url_ready(testlink_pdf_doc):
    normalized, meta = await normalize_intake_documents([testlink_pdf_doc])
    agent = ReadinessAgent(get_memory())
    result = await agent.execute(
        {
            "documents": normalized,
            "interpreted_intake": meta.get("interpreted_intake"),
            "metadata": {"target_url": "https://bank-demo.up.railway.app"},
        }
    )
    assert result["status"] in {"READY", "WARN"}
    assert result.get("block_run") is False
    assert "target_url" not in result.get("gaps", [])
    assert result.get("automatable_obligations", 0) >= 5
