import os

from src.knowledge.embeddings import AZURE_EMBEDDING_DIM, LOCAL_HASH_EMBEDDING_DIM, embedding_dimensions


def test_embedding_dimensions_defaults_to_1536_when_azure_deployment_set(monkeypatch):
    monkeypatch.delenv("APTP_EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    assert embedding_dimensions() == AZURE_EMBEDDING_DIM == 1536


def test_embedding_dimensions_explicit_override(monkeypatch):
    monkeypatch.setenv("APTP_EMBEDDING_DIMENSIONS", "384")
    monkeypatch.setenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
    assert embedding_dimensions() == 384


def test_embedding_dimensions_local_without_azure(monkeypatch):
    monkeypatch.delenv("APTP_EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", raising=False)
    assert embedding_dimensions() == LOCAL_HASH_EMBEDDING_DIM
