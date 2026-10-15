"""Tests for intake guardrails."""
import pytest

from src.intake.guardrails import (
    validate_documents_for_pipeline,
    validate_upload_batch,
    validate_upload_file,
)


def test_blocked_executable_rejected():
    violations = validate_upload_file("malware.exe", b"MZ fake exe content")
    assert violations
    assert violations[0].code == "blocked_file_type"


def test_blocked_script_rejected():
    violations = validate_upload_file("run.sh", b"#!/bin/bash\nrm -rf /")
    assert violations
    assert violations[0].code == "blocked_file_type"


def test_valid_pdf_passes():
    content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nSome QA test requirements for portal login."
    violations = validate_upload_file("spec.pdf", content)
    assert not violations


def test_encrypted_pdf_rejected():
    content = b"%PDF-1.4\n/Encrypt\n"
    violations = validate_upload_file("locked.pdf", content)
    assert any(v.code == "encrypted_pdf" for v in violations)


def test_empty_project_rejected():
    result = validate_documents_for_pipeline([])
    assert not result.accepted
    assert any(v.code == "no_documents" for v in result.violations)


def test_no_qa_relevant_documents_rejected():
    docs = [
        {
            "filename": "invoice-only.txt",
            "document_type": "OTHER",
            "raw_text": "Invoice #12345 for consulting services only.",
        }
    ]
    result = validate_documents_for_pipeline(docs)
    assert not result.accepted
    assert any(v.code == "no_qa_relevant_documents" for v in result.violations)


def test_valid_credential_excel_passes():
    docs = [
        {
            "filename": "usuarios_credenciales.xlsx",
            "document_type": "OTHER",
            "raw_text": "usuario password login test data for QA portal staging",
            "extra": {"soft_intake_role": "ASSET_BUNDLE"},
        },
        {
            "filename": "set-de-pruebas.pdf",
            "document_type": "OTHER",
            "raw_text": "Caso de prueba EMPRESARIAL-001 pasos precondicion validacion QA",
            "extra": {"soft_intake_role": "PRIMARY_SPEC"},
        },
    ]
    result = validate_documents_for_pipeline(docs)
    assert result.accepted
    assert result.qa_relevant_count >= 2
    assert result.pii_flagged_paths


def test_upload_batch_blocks_exe():
    batch = validate_upload_batch([("tools/setup.exe", b"MZ", "application/octet-stream")])
    assert not batch.accepted
