"""Auto-detect intake profile without env vars."""
import os

import pytest

from src.intake.intake_mode import detect_intake_profile


def test_portfolio_types_detected(monkeypatch):
    monkeypatch.delenv("APTP_INTAKE_MODE", raising=False)
    docs = [
        {"document_type": "BRD", "filename": "brd.txt", "raw_text": "Business requirements"},
        {"document_type": "PRD", "filename": "prd.txt", "raw_text": "Product requirements"},
        {"document_type": "UC", "filename": "uc.txt", "raw_text": "Use cases"},
        {"document_type": "AC", "filename": "ac.txt", "raw_text": "Acceptance criteria"},
    ]
    profile = detect_intake_profile(docs)
    assert profile.primary_strategy == "portfolio"
    assert profile.has_full_portfolio
    assert profile.legacy_mode == "portfolio_legacy"


def test_unstructured_pdf_detected(monkeypatch):
    monkeypatch.delenv("APTP_INTAKE_MODE", raising=False)
    docs = [
        {
            "document_type": "OTHER",
            "filename": "client-spec.pdf",
            "raw_text": "Set de pruebas portal empresarial pasos precondicion validacion",
        }
    ]
    profile = detect_intake_profile(docs)
    assert profile.primary_strategy == "interpretation"
    assert profile.has_unstructured_content
    assert profile.legacy_mode == "content_first"


def test_mixed_intake_detected():
    docs = [
        {"document_type": "BRD", "filename": "brd.txt", "raw_text": "BRD content"},
        {
            "document_type": "OTHER",
            "filename": "entregable-pruebas.pdf",
            "raw_text": "Casos de prueba adicionales pasos QA",
        },
    ]
    profile = detect_intake_profile(docs)
    assert profile.is_mixed
    assert profile.primary_strategy == "mixed"


def test_no_env_fallback(monkeypatch):
    monkeypatch.delenv("APTP_INTAKE_MODE", raising=False)
    docs = [{"document_type": "OTHER", "filename": "notes.md", "raw_text": "random notes"}]
    profile = detect_intake_profile(docs)
    assert os.getenv("APTP_INTAKE_MODE") is None
    assert profile.primary_strategy == "interpretation"
