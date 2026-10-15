"""Normalize parsed documents to canonical schema."""
from src.intake.schemas.canonical import CanonicalDocument
from src.intake.metadata import MetadataExtractor


def normalize(doc: CanonicalDocument) -> CanonicalDocument:
    return MetadataExtractor().extract(doc)
