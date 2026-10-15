"""Run-scoped memory bus — publish/subscribe + pgvector knowledge retrieval."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from src.memory.enhanced_memory import EnhancedSharedMemory, MemoryTier, get_memory

logger = logging.getLogger("memory_bus")

def _requirements_list(raw: Any) -> List[Dict[str, Any]]:
    """Normalize requirements from agent block dict or plain list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        inner = raw.get("requirements")
        if isinstance(inner, list):
            return inner
    return []


RUN_ARTIFACT_KEYS = (
    "documents",
    "requirements",
    "features",
    "functional_scenarios",
    "coverage",
    "traceability_matrix",
    "risk",
    "test_plan",
    "generated_code",
    "execution_results",
    "rca",
    "healing",
    "defects",
    "automation_context",
    "knowledge_matches",
    "knowledge_indexed",
)


class RunMemoryBus:
    """Publish pipeline artifacts, retrieve pgvector knowledge, build LLM bundles."""

    def __init__(self, memory: EnhancedSharedMemory, run_id: str):
        self.memory = memory
        self.run_id = run_id
        self.scope = f"run:{run_id}"

    def key(self, name: str) -> str:
        return EnhancedSharedMemory.scoped_key(self.scope, name)

    def publish(
        self,
        name: str,
        value: Any,
        *,
        tier: MemoryTier = MemoryTier.WORKING,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.memory.write(self.key(name), value, tier=tier, tags=tags or [name])

    def read(self, name: str, default: Any = None) -> Any:
        val = self.memory.read_with_fallback(
            self.key(name),
            fallback_keys=[name],
        )
        return default if val is None else val

    def publish_execution_namespace(self, namespace: str, value: Any) -> None:
        self.memory.write(
            EnhancedSharedMemory.execution_key(self.run_id, namespace),
            value,
            tier=MemoryTier.WORKING,
            tags=["execution", namespace],
        )

    async def index_knowledge(
        self,
        project_id: str,
        items: List[Dict[str, Any]],
        *,
        replace_types: Optional[List[str]] = None,
    ) -> int:
        from src.knowledge.indexer import get_indexer

        count = await get_indexer().index_chunks(
            project_id,
            items,
            run_id=self.run_id,
            replace_types=replace_types,
        )
        self.publish("knowledge_indexed", {"count": count, "project_id": project_id})
        return count

    async def index_requirements(self, project_id: str, requirements: List[Dict[str, Any]]) -> int:
        items = [
            {
                "chunk_type": "requirement",
                "external_id": r.get("id"),
                "title": r.get("id"),
                "content": r.get("description", ""),
                "metadata": {"priority": r.get("priority"), "type": r.get("type")},
            }
            for r in requirements
            if r.get("description")
        ]
        return await self.index_knowledge(project_id, items, replace_types=["requirement"])

    async def index_obligations(self, project_id: str, obligations: List[Dict[str, Any]]) -> int:
        items = []
        for ob in obligations:
            if not isinstance(ob, dict):
                continue
            title = (ob.get("title") or ob.get("id") or "").strip()
            if not title:
                continue
            parts = [title]
            parts.extend(str(s) for s in (ob.get("preconditions") or []) if s)
            parts.extend(str(s) for s in (ob.get("steps") or []) if s)
            parts.extend(str(s) for s in (ob.get("expected_results") or []) if s)
            items.append(
                {
                    "chunk_type": "obligation",
                    "external_id": ob.get("id") or ob.get("external_id"),
                    "title": title[:512],
                    "content": "\n".join(parts)[:8000],
                    "metadata": {
                        "automation_tier": ob.get("automation_tier"),
                        "external_id": ob.get("external_id"),
                    },
                }
            )
        if not items:
            return 0
        return await self.index_knowledge(project_id, items, replace_types=["obligation"])

    async def index_pipeline_results(self, project_id: str, results: Dict[str, Any]) -> int:
        items: List[Dict[str, Any]] = []

        for sc in (results.get("test_design") or {}).get("functional_scenarios", []):
            items.append(
                {
                    "chunk_type": "scenario",
                    "external_id": sc.get("id"),
                    "title": sc.get("title"),
                    "content": json.dumps(sc, default=str)[:8000],
                    "metadata": {"requirement_ids": sc.get("requirement_ids")},
                }
            )

        for d in (results.get("defects") or {}).get("defects", []):
            items.append(
                {
                    "chunk_type": "defect",
                    "external_id": d.get("id"),
                    "title": d.get("title"),
                    "content": f"{d.get('title')}: {d.get('description', '')}",
                    "metadata": {"severity": d.get("severity"), "category": d.get("category")},
                }
            )

        rca = results.get("rca") or {}
        if rca.get("root_cause"):
            items.append(
                {
                    "chunk_type": "rca",
                    "external_id": f"rca-{self.run_id}",
                    "title": "Root cause analysis",
                    "content": json.dumps(rca, default=str)[:8000],
                }
            )

        healing = results.get("healing") or {}
        if healing.get("changes_applied"):
            items.append(
                {
                    "chunk_type": "healing",
                    "external_id": f"heal-{self.run_id}",
                    "title": "Self-healing changes",
                    "content": json.dumps(healing, default=str)[:8000],
                }
            )

        if not items:
            return 0
        return await self.index_knowledge(
            project_id,
            items,
            replace_types=["scenario", "defect", "rca", "healing"],
        )

    async def retrieve_knowledge(
        self,
        project_id: str,
        query: str,
        *,
        limit: int = 8,
        chunk_types: Optional[List[str]] = None,
        include_other_runs: bool = True,
    ) -> List[Dict[str, Any]]:
        from src.knowledge.indexer import get_indexer

        run_filter = None if include_other_runs else self.run_id
        matches = await get_indexer().search(
            project_id,
            query,
            limit=limit,
            chunk_types=chunk_types,
            run_id=run_filter,
        )
        self.publish(
            "knowledge_matches",
            {"query": query, "matches": matches, "count": len(matches)},
            tier=MemoryTier.WORKING,
        )
        return matches

    def format_knowledge_for_prompt(self, matches: List[Dict[str, Any]], max_chars: Optional[int] = None) -> str:
        from src.agents.token_policy import prompt_budget

        lines = []
        for m in matches:
            desc = m.get("description", "")
            lines.append(
                f"- [{m.get('chunk_type')}] {m.get('external_id')}: "
                f"{desc} (similarity={m.get('similarity')})"
            )
        text = "\n".join(lines)
        budget = max_chars if max_chars is not None else prompt_budget("KnowledgeRetrievalAgent", len(text))
        return text if len(text) <= budget else f"{text[:budget]}... [truncated]"

    async def build_automation_bundle(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Merge explicit context + memory bus + pgvector retrieval for automation agents."""
        project_id = context.get("project_id") or "demo"
        bundle: Dict[str, Any] = {
            "run_id": self.run_id,
            "project_id": project_id,
            "target_url": context.get("target_url") or (context.get("metadata") or {}).get("target_url"),
            "metadata": context.get("metadata") or {},
            "requirements": _requirements_list(
                context.get("requirements") or self.read("requirements", [])
            ),
            "functional_scenarios": context.get("functional_scenarios")
            or self.read("functional_scenarios", []),
            "traceability_matrix": context.get("traceability_matrix")
            or self.read("traceability_matrix", []),
            "coverage": context.get("coverage") or self.read("coverage", {}),
            "test_plan": context.get("test_plan") or self.read("test_plan", {}),
            "generated_code": context.get("generated_code") or self.read("generated_code", {}),
            "execution_results": context.get("execution_results") or self.read("execution_results", {}),
            "rca": context.get("rca") or self.read("rca", {}),
            "healing": context.get("healing") or self.read("healing", {}),
        }
        if context.get("acceptance_criteria"):
            bundle["acceptance_criteria"] = context["acceptance_criteria"]

        query = context.get("knowledge_query") or self._default_knowledge_query(bundle)
        if query and project_id:
            matches = await self.retrieve_knowledge(
                project_id,
                query,
                limit=context.get("knowledge_limit", 10),
                chunk_types=context.get("knowledge_chunk_types"),
            )
            bundle["knowledge_matches"] = matches
            bundle["knowledge_context"] = self.format_knowledge_for_prompt(matches)

        self.publish("automation_context", bundle, tier=MemoryTier.WORKING)
        return bundle

    @staticmethod
    def _default_knowledge_query(bundle: Dict[str, Any]) -> str:
        parts = []
        for r in _requirements_list(bundle.get("requirements"))[:3]:
            parts.append(r.get("description", ""))
        for s in (bundle.get("functional_scenarios") or [])[:2]:
            parts.append(s.get("title", ""))
        rca = bundle.get("rca") or {}
        if rca.get("root_cause"):
            parts.append(str(rca.get("root_cause")))
        return " ".join(p for p in parts if p).strip()[:500]


def bus_for_run(run_id: str, memory: Optional[EnhancedSharedMemory] = None) -> RunMemoryBus:
    return RunMemoryBus(memory or get_memory(), run_id)
