"""Intake normalization with AI interpretation for unstructured deliverables."""
from pathlib import Path

import pytest

from src.agents.intake_interpretation import IntakeInterpretationAgent
from src.intake.normalization import (
    merge_document_sets,
    missing_mandatory_types,
    needs_ai_interpretation,
    normalize_intake_documents,
)
from src.memory.enhanced_memory import get_memory


UNSTRUCTURED_DOC = {
    "id": "client-notes",
    "filename": "client-notes.pdf",
    "document_type": "OTHER",
    "raw_text": """
    Project: Acme Shop R2
    Target URL: https://shop.acme.example.com
    The business wants a checkout flow that reduces cart abandonment.
    Users must add items, apply coupons, pay with card, and receive email confirmation.
    QA should verify login, product search, cart, checkout, and order history.
    Acceptance: Given a logged-in user with items in cart When they checkout Then order confirmation appears.
    UC-001 User completes purchase. Actor: Customer. Flow: browse, add to cart, checkout, pay.
    """,
}


@pytest.mark.asyncio
async def test_unstructured_single_doc_needs_ai():
    assert needs_ai_interpretation([UNSTRUCTURED_DOC], after_regex=[UNSTRUCTURED_DOC]) is True


@pytest.mark.asyncio
async def test_ai_interpretation_produces_mandatory_types():
    agent = IntakeInterpretationAgent(get_memory())
    normalized, meta = await normalize_intake_documents([UNSTRUCTURED_DOC], agent)
    assert meta["ai_invoked"] is True
    assert meta["method"].startswith("ai_interpretation")
    types = {d["document_type"] for d in normalized}
    assert types >= {"BRD", "PRD", "UC", "AC"}


@pytest.mark.asyncio
async def test_structured_pdf_skips_ai_when_regex_sufficient():
    pdf = Path(__file__).parent.parent / "docs" / "client-qa-intake-jsh-portfolio-r1.pdf"
    if not pdf.is_file():
        pytest.skip("sample PDF missing")
    from src.intake.parser import DocumentParser

    parsed = DocumentParser().parse_file(pdf).to_dict()
    agent = IntakeInterpretationAgent(get_memory())
    normalized, meta = await normalize_intake_documents([parsed], agent)
    assert meta["method"] in {"regex_split", "native_types"}
    assert meta["ai_invoked"] is False
    assert not missing_mandatory_types(normalized)


def test_merge_prefers_existing_types():
    primary = [{"document_type": "BRD", "raw_text": "business", "id": "1"}]
    secondary = [
        {"document_type": "BRD", "raw_text": "ai business", "id": "2"},
        {"document_type": "PRD", "raw_text": "product", "id": "3"},
    ]
    merged = merge_document_sets(primary, secondary)
    by_type = {d["document_type"]: d for d in merged}
    assert by_type["BRD"]["id"] == "1"
    assert by_type["PRD"]["id"] == "3"
