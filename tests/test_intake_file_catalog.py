"""Intake file catalog — purpose detection and PDF↔asset linking."""
from pathlib import Path

import pytest

from src.agents.test_asset_resolver import TestAssetResolverAgent
from src.intake.asset_bundle_parser import parse_asset_bundle_document
from src.intake.extraction.document_extractor import DocumentExtractor
from src.intake.file_catalog import build_intake_catalog, classify_file_purpose, enrich_documents_with_catalog
from src.intake.parser import DocumentParser
from src.services.run_secret_store import RunSecretStore

FIXTURE_PDF_TEXT = Path(__file__).parent / "fixtures" / "documents" / "inc1353166-testlink.txt"
XLSX = Path(__file__).parent / "fixtures" / "documents" / "test-credentials.xlsx"


@pytest.fixture
def pdf_doc():
    return {
        "id": "pdf-1",
        "filename": "pruebas-inc1353166.pdf",
        "document_type": "OTHER",
        "raw_text": FIXTURE_PDF_TEXT.read_text(encoding="utf-8", errors="replace") if FIXTURE_PDF_TEXT.is_file() else "Caso de Prueba EMPRESARIAL-13330",
        "extra": {"format": "pdf", "intake_role": "PRIMARY_SPEC"},
    }


@pytest.fixture
def xlsx_doc():
    if not XLSX.is_file():
        pytest.skip("test-credentials.xlsx fixture missing")
    parsed = DocumentParser().parse_file(XLSX)
    doc = parsed.to_dict()
    doc["id"] = "xlsx-1"
    doc["drive_file_id"] = "xlsx-1"
    doc["extra"]["intake_role"] = "ASSET_BUNDLE"
    return doc


def test_classify_pdf_as_primary_spec(pdf_doc):
    meta = classify_file_purpose(pdf_doc)
    assert meta["intake_role"] == "PRIMARY_SPEC"
    assert "obligation_extraction" in meta["usage_when"]


def test_classify_xlsx_as_asset_bundle(xlsx_doc):
    meta = classify_file_purpose(xlsx_doc)
    assert meta["intake_role"] == "ASSET_BUNDLE"
    assert "credential" in meta["asset_types"]
    assert "playwright_login" in meta["usage_when"]


def test_catalog_links_xlsx_to_pdf(pdf_doc, xlsx_doc):
    enriched, catalog = enrich_documents_with_catalog([pdf_doc, xlsx_doc])
    assert catalog["primary_spec_filename"] == "pruebas-inc1353166.pdf"
    assert len(catalog["support_files"]) == 1
    xlsx = next(d for d in enriched if d["filename"].endswith(".xlsx"))
    assert xlsx["extra"]["linked_primary_id"] == catalog["primary_spec_id"]
    assert xlsx["extra"]["file_purpose"]


def test_parse_credentials_from_usuarios_sheet(xlsx_doc):
    assets = parse_asset_bundle_document(xlsx_doc)
    login_assets = [a for a, _ in assets if a.key == "login_user"]
    assert login_assets
    assert login_assets[0].asset_type == "credential"
    _, raw = next((a, r) for a, r in assets if a.key == "login_user")
    assert raw.get("cliente") == "312"
    assert raw.get("documento") == "20242587"
    assert raw.get("clave") == "123456"
    assert raw.get("token") == "63866019"


@pytest.mark.asyncio
async def test_asset_resolver_uses_xlsx_credentials(xlsx_doc, pdf_doc, monkeypatch):
    monkeypatch.setattr("src.agents.test_asset_resolver.mcp_intake_available", lambda: False)
    enriched, catalog = enrich_documents_with_catalog([pdf_doc, xlsx_doc])
    for d in enriched:
        d["intake_catalog"] = catalog

    agent = TestAssetResolverAgent()
    interpreted = {
        "intake_mode": "content_first",
        "confidence": 0.9,
        "environment": {"target_url": "https://bank-demo.up.railway.app"},
        "obligations": [
            {
                "id": "OBL-EMP-13330",
                "title": "Crear inscripción",
                "steps": ["Ingresar al portal con credenciales"],
                "required_assets": ["login_user"],
                "automation_tier": "automated",
            }
        ],
        "assets": [],
        "asset_bindings": [],
    }
    result = await agent.execute(
        {
            "run_id": "run-xlsx-creds",
            "project_id": "APTP-real-pdf-intake",
            "interpreted_intake": interpreted,
            "documents": enriched,
        }
    )
    assert result["asset_count"] >= 1
    assert any(a["key"] == "login_user" for a in result["assets"])
    store = RunSecretStore.for_run("run-xlsx-creds")
    assert store.get("login_user.cliente") == "312"
    assert store.get("login_user.documento") == "20242587"
    assert result.get("intake_catalog")
