"""Combined client PDF splitting for readiness and downstream agents."""
from pathlib import Path

import pytest

from src.connectors.google_drive.intake import ingest_from_sync_result
from src.intake.combined import expand_combined_intake_documents, is_combined_intake_document
from src.intake.discovery import DiscoveredFile, DiscoveryResult, DiscoverySource
from src.intake.parser import DocumentParser
from src.intake.schemas.canonical import DocumentTypeEnum
from src.services.intake_registry import SyncResult


PDF_PATH = Path(__file__).parent.parent / "docs" / "client-qa-intake-jsh-portfolio-r1.pdf"


@pytest.fixture
def combined_pdf_doc():
    if not PDF_PATH.is_file():
        pytest.skip("sample PDF missing")
    parsed = DocumentParser().parse_file(PDF_PATH)
    return parsed.to_dict()


def test_combined_pdf_detected(combined_pdf_doc):
    assert is_combined_intake_document(combined_pdf_doc)


def test_combined_pdf_splits_into_mandatory_types(combined_pdf_doc):
    expanded = expand_combined_intake_documents([combined_pdf_doc])
    types = {d["document_type"] for d in expanded}
    assert types >= {"BRD", "PRD", "UC", "AC"}
    assert len(expanded) >= 4


@pytest.mark.asyncio
async def test_readiness_would_pass_after_split(combined_pdf_doc):
    from src.agents.readiness import ReadinessAgent
    from src.memory.enhanced_memory import get_memory

    expanded = expand_combined_intake_documents([combined_pdf_doc])
    agent = ReadinessAgent(get_memory())
    result = await agent.execute({"documents": expanded})
    assert result["status"] == "READY"
    assert result["missing_artifacts"] == []


def test_split_is_idempotent(combined_pdf_doc):
    once = expand_combined_intake_documents([combined_pdf_doc])
    twice = expand_combined_intake_documents(once)
    assert len(once) == len(twice)


@pytest.mark.asyncio
async def test_ingest_expands_combined_pdf_from_drive_sync():
    if not PDF_PATH.is_file():
        pytest.skip("sample PDF missing")

    pdf = DiscoveredFile(
        file_id="f1",
        name="client-qa-intake-jsh-portfolio-r1.pdf",
        relative_path="client-qa-intake-jsh-portfolio-r1.pdf",
        fingerprint="abc:70000:f1",
        inferred_doc_type=DocumentTypeEnum.BRD,
        source=DiscoverySource.LOCAL,
        local_path=str(PDF_PATH),
    )
    discovery = DiscoveryResult(
        root_name="test",
        root_folder_id="folder-1",
        folders=[],
        files=[pdf],
        source=DiscoverySource.LOCAL,
    )
    sync = SyncResult(
        root_id="root-1",
        project_id="JSH-PORTFOLIO-2026",
        discovery=discovery,
        files_to_process=[pdf],
    )
    docs = await ingest_from_sync_result(sync)
    types = {d["document_type"] for d in docs}
    assert types >= {"BRD", "PRD", "UC", "AC"}
