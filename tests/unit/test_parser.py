from pathlib import Path

import pytest

from src.intake.parser import DocumentParser, infer_document_type
from src.intake.metadata import MetadataExtractor
from src.intake.schemas.canonical import DocumentTypeEnum

FIXTURES = Path(__file__).parent.parent / "fixtures" / "documents"


@pytest.fixture
def parser():
    return DocumentParser()


def test_infer_brd_from_filename():
    assert infer_document_type("sample_brd.txt", "") == DocumentTypeEnum.BRD


def test_infer_prd_from_content():
    assert infer_document_type("doc.txt", "PRODUCT REQUIREMENTS DOCUMENT") == DocumentTypeEnum.PRD


def test_parse_brd_fixture(parser):
    path = FIXTURES / "brd.txt"
    doc = parser.parse_file(path)
    assert doc.document_type == DocumentTypeEnum.BRD
    assert "REQ-001" in doc.raw_text
    assert len(doc.sections) >= 1


def test_parse_all_fixtures(parser):
    for name in ("brd.txt", "prd.txt", "uc.txt", "ac.txt"):
        doc = parser.parse_file(FIXTURES / name)
        assert doc.raw_text


def test_metadata_extractor(parser):
    doc = parser.parse_file(FIXTURES / "prd.txt")
    enriched = MetadataExtractor().extract(doc)
    assert enriched.metadata.version == "2.0"
    assert enriched.metadata.release == "R1"
