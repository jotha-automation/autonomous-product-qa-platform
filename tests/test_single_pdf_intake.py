"""Regression: single PDF client deliverable and double-sync intake."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.connectors.google_drive.intake import ingest_from_sync_result
from src.intake.discovery import (
    DiscoveredFile,
    DiscoveryResult,
    DiscoverySource,
    classify_document,
)
from src.intake.metadata import enrich_metadata_from_documents, extract_intake_metadata_from_text
from src.intake.schemas.canonical import DocumentTypeEnum
from src.services.intake_registry import SyncResult, compute_file_changes


def test_classify_combined_intake_pdf_by_filename():
    doc_type = classify_document(
        "client-qa-intake-jsh-portfolio-r1.pdf",
        "client-qa-intake-jsh-portfolio-r1.pdf",
    )
    assert doc_type == DocumentTypeEnum.BRD


def test_extract_target_url_from_pdf_text():
    text = "Target URL: https://jsh.up.railway.app/\nProject ID: JSH-PORTFOLIO-2026"
    meta = extract_intake_metadata_from_text(text)
    assert meta["target_url"] == "https://jsh.up.railway.app/"
    assert meta["project_id"] == "JSH-PORTFOLIO-2026"


def test_enrich_metadata_from_parsed_documents():
    docs = [{"raw_text": "Target URL: https://example.com/app\nRelease: R2"}]
    merged = enrich_metadata_from_documents({}, docs)
    assert merged["target_url"] == "https://example.com/app"
    assert merged["release"] == "R2"


def test_double_sync_leaves_nothing_to_process_without_force():
    """After first sync indexes a file, a second diff must not block pipeline intake."""
    pdf = DiscoveredFile(
        file_id="f1",
        name="client-qa-intake-jsh-portfolio-r1.pdf",
        relative_path="client-qa-intake-jsh-portfolio-r1.pdf",
        fingerprint="abc:70000:f1",
        inferred_doc_type=DocumentTypeEnum.BRD,
        source=DiscoverySource.DRIVE,
    )
    prev = {
        pdf.relative_path: SimpleNamespace(content_fingerprint=pdf.fingerprint, drive_file_id="f1")
    }
    _, to_process = compute_file_changes(prev, [pdf])
    assert to_process == []


@pytest.mark.asyncio
async def test_combined_pdf_processed_when_already_indexed():
    pdf = DiscoveredFile(
        file_id="f1",
        name="client-qa-intake-jsh-portfolio-r1.pdf",
        relative_path="client-qa-intake-jsh-portfolio-r1.pdf",
        fingerprint="abc:70000:f1",
        inferred_doc_type=DocumentTypeEnum.BRD,
        source=DiscoverySource.LOCAL,
        local_path=str(
            Path(__file__).parent.parent / "docs" / "client-qa-intake-jsh-portfolio-r1.pdf"
        ),
    )
    if not Path(pdf.local_path).is_file():
        pytest.skip("sample PDF not generated")

    discovery = DiscoveryResult(
        root_name="test",
        root_folder_id="folder-1",
        folders=[],
        files=[pdf],
        source=DiscoverySource.LOCAL,
    )
    assert discovery.has_combined_intake_file()

    sync = SyncResult(
        root_id="root-1",
        project_id="JSH-PORTFOLIO-2026",
        discovery=discovery,
        files_to_process=[pdf],
    )
    docs = await ingest_from_sync_result(sync, prefer_mandatory=True)
    assert len(docs) >= 4
    types = {d["document_type"] for d in docs}
    assert types >= {"BRD", "PRD", "UC", "AC"}
    assert "jsh.up.railway.app" in docs[0]["raw_text"]
