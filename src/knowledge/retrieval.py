"""Product knowledge retrieval via pgvector."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.knowledge.indexer import get_indexer


@dataclass
class ContextWindow:
    items: List[str] = field(default_factory=list)
    max_tokens: int = 4000


class KnowledgeRetriever:
    """Semantic retrieval over indexed pipeline knowledge."""

    async def search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 8,
        chunk_types: Optional[List[str]] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return await get_indexer().search(
            project_id,
            query,
            limit=limit,
            chunk_types=chunk_types,
            run_id=run_id,
        )

    async def build_prompt_context(
        self,
        project_id: str,
        query_type: str,
        query_data: Dict[str, Any],
        context_window: Optional[ContextWindow] = None,
    ) -> Tuple[str, ContextWindow]:
        window = context_window or ContextWindow()
        query = query_data.get("query", "")
        if not query:
            return "", window

        types = query_data.get("chunk_types")
        matches = await self.search(
            project_id,
            query,
            limit=query_data.get("limit", 8),
            chunk_types=types,
            run_id=query_data.get("run_id"),
        )
        lines = []
        for m in matches:
            line = (
                f"[{m.get('chunk_type')}] {m.get('external_id') or m.get('title')}: "
                f"{m.get('description', '')[:400]} (sim={m.get('similarity', 0)})"
            )
            lines.append(line)
            window.items.append(line)

        return "\n".join(lines), window


_retriever: Optional[KnowledgeRetriever] = None


def get_retriever() -> KnowledgeRetriever:
    global _retriever
    if _retriever is None:
        _retriever = KnowledgeRetriever()
    return _retriever
