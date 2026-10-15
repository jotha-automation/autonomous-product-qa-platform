from typing import Any, Dict

from src.agents.base import BaseAgent
from src.agents.memory_bus import _requirements_list, bus_for_run
from src.agents.token_policy import output_tokens


class KnowledgeRetrievalAgent(BaseAgent):
    """Retrieve historical QA knowledge for current release planning.

    Memory contract:
      Reads: requirements
      Writes: knowledge_matches
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        project_id = context.get("project_id", "default")
        run_id = context.get("run_id") or context.get("execution_id")
        requirements = _requirements_list(
            context.get("requirements") or self.read_memory("requirements", [])
        )
        query = context.get("query", "")
        if not query and requirements:
            query = " ".join(r.get("description", "") for r in requirements if r.get("description"))

        bus = bus_for_run(run_id, self.memory) if run_id else None
        matches: list = []
        if bus:
            try:
                matches = await bus.retrieve_knowledge(
                    project_id,
                    query,
                    limit=context.get("limit", 10),
                    chunk_types=context.get("chunk_types"),
                )
            except Exception:
                matches = []
        else:
            try:
                from src.knowledge.indexer import get_indexer

                matches = await get_indexer().search(project_id, query, limit=10)
            except Exception:
                matches = []

        try:
            hist, _ = await self.retriever.build_prompt_context(
                project_id,
                "requirements",
                {"query": query, "limit": 10, "run_id": run_id},
            )
        except Exception:
            hist = ""

        task = f"""TASK: Synthesize historical QA knowledge for current release planning.

Return JSON:
{{
  "matches": [{{ "chunk_type": string, "external_id": string, "similarity": number, "insight": string }}],
  "patterns": [string],
  "risks_from_history": [string],
  "recommendations": [string]
}}

QUERY: {query}
RETRIEVED CHUNKS:
{hist or self.compact_json(matches)}
"""
        result = await self.ask_json(
            task,
            {"matches": matches, "query": query, "patterns": [], "recommendations": []},
            temperature=0.1,
            max_tokens=output_tokens(self.agent_name, item_count=len(matches)),
            use_fallback_on_failure=True,
        )
        result.setdefault("matches", matches)
        result["query"] = query
        self.write_memory("knowledge_matches", result.get("matches", matches))
        if bus:
            bus.publish("knowledge_synthesis", result)
        return result
