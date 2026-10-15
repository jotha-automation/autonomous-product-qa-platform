"""Generic document extraction (PDF/Excel/etc.) without business-schema assumptions."""
from src.intake.extraction.document_extractor import DocumentExtractor, extract_document

__all__ = ["DocumentExtractor", "extract_document"]
