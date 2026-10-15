"""Index and search knowledge chunks via pgvector."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select

from src.knowledge.db import get_session
from src.knowledge.embeddings import embed_text, embedding_dimensions
from src.knowledge.models import KnowledgeChunk

logger = logging.getLogger("knowledge.indexer")

try:
    from pgvector.sqlalchemy import Vector  # noqa: F401

    _PGVECTOR = True
except ImportError:
    _PGVECTOR = False


class RequirementIndexer:
    """Indexes pipeline artifacts and retrieves similar chunks."""

    async def index_chunks(
        self,
        project_id: str,
        items: List[Dict[str, Any]],
        *,
        run_id: Optional[str] = None,
        replace_types: Optional[List[str]] = None,
    ) -> int:
        if not items:
            return 0
        if not _PGVECTOR:
            logger.warning("pgvector not installed — skipping knowledge index")
            return 0

        indexed = 0
        async with get_session() as session:
            if replace_types:
                await session.execute(
                    delete(KnowledgeChunk).where(
                        KnowledgeChunk.project_id == project_id,
                        KnowledgeChunk.chunk_type.in_(replace_types),
                    )
                )

            for item in items:
                content = (item.get("content") or item.get("description") or "").strip()
                if not content:
                    continue
                emb = await embed_text(content)
                row = KnowledgeChunk(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    run_id=run_id or item.get("run_id"),
                    chunk_type=item.get("chunk_type", "requirement"),
                    external_id=item.get("external_id") or item.get("id"),
                    title=(item.get("title") or item.get("external_id") or "")[:512],
                    content=content[:8000],
                    chunk_metadata=item.get("metadata") or {},
                    embedding=emb,
                )
                session.add(row)
                indexed += 1
            await session.commit()
        logger.info("Indexed %d knowledge chunks for project %s", indexed, project_id)
        return indexed

    async def index(self, project_id: str, requirements: List[Dict[str, Any]]) -> int:
        items = [
            {
                "chunk_type": "requirement",
                "external_id": r.get("id"),
                "content": r.get("description", ""),
                "title": r.get("id"),
                "metadata": {"priority": r.get("priority")},
            }
            for r in requirements
        ]
        return await self.index_chunks(
            project_id, items, replace_types=["requirement"]
        )

    async def search(
        self,
        project_id: str,
        query: str,
        limit: int = 8,
        *,
        chunk_types: Optional[List[str]] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not query.strip():
            return []
        if not _PGVECTOR:
            return self._stub_search(project_id, query, limit)

        query_emb = await embed_text(query)
        distance_col = KnowledgeChunk.embedding.cosine_distance(query_emb).label("dist")
        async with get_session() as session:
            stmt = select(KnowledgeChunk, distance_col).where(
                KnowledgeChunk.project_id == project_id
            )
            if chunk_types:
                stmt = stmt.where(KnowledgeChunk.chunk_type.in_(chunk_types))
            if run_id:
                stmt = stmt.where(
                    (KnowledgeChunk.run_id == run_id) | (KnowledgeChunk.run_id.is_(None))
                )
            stmt = stmt.order_by(distance_col).limit(limit)
            result = await session.execute(stmt)
            rows = result.all()

        out: List[Dict[str, Any]] = []
        for row, dist in rows:
            similarity = 1.0 - float(dist) if dist is not None else 0.75
            out.append(
                {
                    "chunk_id": row.id,
                    "chunk_type": row.chunk_type,
                    "external_id": row.external_id,
                    "title": row.title,
                    "description": row.content[:500],
                    "similarity": round(max(0.0, min(1.0, similarity)), 4),
                    "project_id": project_id,
                    "run_id": row.run_id,
                    "metadata": row.chunk_metadata or {},
                }
            )
        return out

    def _stub_search(self, project_id: str, query: str, limit: int) -> List[Dict[str, Any]]:
        return [
            {
                "requirement_id": "REQ-001",
                "similarity": 0.9,
                "description": f"Similar to: {query[:80]}",
                "project_id": project_id,
                "chunk_type": "requirement",
            }
        ][:limit]


_indexer: Optional[RequirementIndexer] = None


def get_indexer() -> RequirementIndexer:
    global _indexer
    if _indexer is None:
        _indexer = RequirementIndexer()
    return _indexer


async def index_requirements(project_id: str, requirements: List[Dict[str, Any]]) -> int:
    return await get_indexer().index(project_id, requirements)
