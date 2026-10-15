"""QA spec chunker — TestLink / INC1353166 style documents."""
from pathlib import Path

import pytest

from src.intake.qa_spec_chunker import (
    chunk_documents,
    chunk_qa_spec_text,
    is_qa_spec_text,
    obligations_from_chunks,
    parse_chunk_to_obligation_dict,
)

FIXTURE = Path(__file__).parent / "fixtures" / "documents" / "inc1353166-testlink.txt"


@pytest.fixture
def testlink_text():
    if not FIXTURE.is_file():
        pytest.skip("INC1353166 fixture missing")
    return FIXTURE.read_text(encoding="utf-8", errors="replace")


def test_is_qa_spec_detects_testlink(testlink_text):
    assert is_qa_spec_text(testlink_text, "pruebas-inc1353166.pdf") is True


def test_chunk_count_matches_empresarial_cases(testlink_text):
    chunks = chunk_qa_spec_text(testlink_text, source_filename="pruebas.pdf")
    assert len(chunks) >= 10
    assert all(c.case_id.startswith("EMPRESARIAL") for c in chunks)


def test_obligations_have_steps_and_tiers(testlink_text):
    chunks = chunk_qa_spec_text(testlink_text, source_filename="pruebas.pdf")
    obls = obligations_from_chunks(chunks)
    assert len(obls) >= 10
    assert any(o["id"].startswith("OBL-EMP-") for o in obls)
    automated = [o for o in obls if o["automation_tier"] == "automated"]
    manual = [o for o in obls if o["automation_tier"] == "manual"]
    assert len(automated) >= 5
    business_day = [o for o in obls if "13327" in o.get("external_id", "") or "día" in o.get("title", "").lower()]
    assert any(o["automation_tier"] == "automated" for o in business_day)
    sample = next(o for o in obls if "13327" in o["id"])
    assert sample["steps"]
    assert sample["preconditions"] or sample["expected_results"]


def test_chunk_documents_from_single_other_doc(testlink_text):
    docs = [
        {
            "filename": "pruebas-inc1353166.pdf",
            "document_type": "OTHER",
            "raw_text": testlink_text,
        }
    ]
    chunks = chunk_documents(docs)
    assert len(chunks) >= 10


def test_parse_single_chunk_structure(testlink_text):
    chunks = chunk_qa_spec_text(testlink_text, source_filename="pruebas.pdf")
    parsed = parse_chunk_to_obligation_dict(chunks[0])
    assert "EMPRESARIAL" in parsed.get("external_id", "") or "EMP" in parsed["id"]
    assert parsed["title"]
