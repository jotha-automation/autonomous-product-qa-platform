"""Text embeddings for pgvector (Azure OpenAI / Foundry — same project endpoint as chat)."""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import List, Optional

logger = logging.getLogger("knowledge.embeddings")

# text-embedding-3-small on Azure OpenAI
AZURE_EMBEDDING_DIM = 1536
LOCAL_HASH_EMBEDDING_DIM = 384


def embedding_dimensions() -> int:
    explicit = (os.getenv("APTP_EMBEDDING_DIMENSIONS") or "").strip()
    if explicit:
        return int(explicit)
    if (os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "").strip():
        return AZURE_EMBEDDING_DIM
    return LOCAL_HASH_EMBEDDING_DIM


def _hash_embedding(text: str, dim: int) -> List[float]:
    """Deterministic local embedding for dev/mock (no API)."""
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    vec: List[float] = []
    while len(vec) < dim:
        for i in range(0, len(seed), 4):
            if len(vec) >= dim:
                break
            chunk = seed[i : i + 4]
            val = int.from_bytes(chunk.ljust(4, b"\0"), "big", signed=False)
            vec.append((val % 10000) / 5000.0 - 1.0)
        seed = hashlib.sha256(seed).digest()
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


async def embed_text(text: str, *, dimensions: Optional[int] = None) -> List[float]:
    from src.runtime_policy import RealDataRequiredError, use_mock_llm

    dim = dimensions or embedding_dimensions()
    cleaned = (text or "").strip()
    if not cleaned:
        return [0.0] * dim

    deployment = (os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or "").strip()

    if not use_mock_llm():
        if not deployment:
            raise RealDataRequiredError(
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT is required (e.g. text-embedding-3-small)."
            )
        vec = await _azure_embed(cleaned, deployment)
        if len(vec) == dim:
            return vec
        if len(vec) == AZURE_EMBEDDING_DIM and dim == AZURE_EMBEDDING_DIM:
            return vec
        logger.warning(
            "Azure embedding dim=%d expected=%d; check APTP_EMBEDDING_DIMENSIONS and DB migration",
            len(vec),
            dim,
        )
        return vec[:dim] if len(vec) > dim else vec + [0.0] * (dim - len(vec))

    if deployment:
        try:
            return await _azure_embed(cleaned, deployment)
        except Exception as exc:
            logger.warning("Azure embedding failed, using hash fallback: %s", exc)

    return _hash_embedding(cleaned, dim)


async def embed_texts(texts: List[str], *, dimensions: Optional[int] = None) -> List[List[float]]:
    return [await embed_text(t, dimensions=dimensions) for t in texts]


async def _azure_embed(text: str, deployment: str) -> List[float]:
    """Use the same Foundry project endpoint as chat (all deployments share one base URL)."""
    from src.services.azure_foundry import create_foundry_openai_client

    client = create_foundry_openai_client()
    logger.info("Azure embedding deployment=%s chars=%d", deployment, len(text))
    resp = await _to_thread(
        client.embeddings.create,
        model=deployment,
        input=text,
    )
    return list(resp.data[0].embedding)


async def _to_thread(fn, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)
