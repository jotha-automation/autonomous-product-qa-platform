"""Requirements analyst agent."""
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.agents.token_policy import intake_doc_char_limit, intake_max_documents, output_tokens


class RequirementsAnalystAgent(BaseAgent):
    """Derive structured requirements from interpreted intake.

    Memory contract:
      Reads: context["documents"], interpreted_intake
      Writes: requirements, features, actors, business_rules
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        documents = context.get("documents", [])
        interpreted = context.get("interpreted_intake") or self.read_memory("interpreted_intake", {})
        obligations = interpreted.get("obligations") or []

        if obligations:
            doc_text = self._summarize_obligations(obligations)
            task = f"""TASK: Derive structured requirements from interpreted test obligations.

Return JSON:
{{
  "requirements": [{{ "id": string, "description": string, "priority": "critical"|"high"|"medium"|"low", "type": "functional"|"non_functional", "acceptance_hints": [string], "obligation_id": string }}],
  "features": [string],
  "actors": [string],
  "business_rules": [string],
  "assumptions": [string],
  "out_of_scope": [string]
}}

OBLIGATIONS:
{doc_text}
"""
            result = await self.ask_json(
                task,
                self._fallback_from_obligations(obligations),
                temperature=0.1,
                max_tokens=output_tokens(self.agent_name, item_count=len(obligations)),
                use_fallback_on_failure=True,
            )
        else:
            doc_text = self._summarize_docs(documents)
            task = f"""TASK: Extract enterprise-grade structured requirements from product documentation.

Return JSON:
{{
  "requirements": [{{ "id": string, "description": string, "priority": "critical"|"high"|"medium"|"low", "type": "functional"|"non_functional", "acceptance_hints": [string] }}],
  "features": [string],
  "actors": [string],
  "business_rules": [string],
  "assumptions": [string],
  "out_of_scope": [string]
}}

DOCUMENTS:
{doc_text}
"""
            result = await self.ask_json(
                task,
                self._fallback_extract(documents),
                temperature=0.1,
                max_tokens=output_tokens(self.agent_name, item_count=len(documents) * 8),
                use_fallback_on_failure=True,
            )

        for key in ("requirements", "features", "actors", "business_rules", "assumptions", "out_of_scope"):
            result.setdefault(key, [])

        self.store_short_term_memory("requirements", result.get("requirements", []))
        self.store_short_term_memory("features", result.get("features", []))
        self.store_short_term_memory("actors", result.get("actors", []))
        self.store_short_term_memory("business_rules", result.get("business_rules", []))
        return result

    def _summarize_docs(self, documents: List[Dict]) -> str:
        parts = []
        per_doc = intake_doc_char_limit()
        for doc in documents[: intake_max_documents()]:
            text = doc.get("raw_text") or ""
            if not text and doc.get("sections"):
                text = "\n".join(s.get("content", "") for s in doc["sections"])
            if len(text) > per_doc:
                text = f"{text[:per_doc]}\n[document truncated at {per_doc} chars]"
            parts.append(
                f"--- {doc.get('filename', 'doc')} ({doc.get('document_type', 'OTHER')}) ---\n{text}"
            )
        combined = "\n".join(parts) or "No documents provided."
        return self.fit_prompt_budget(combined)

    def _fallback_extract(self, documents: List[Dict]) -> Dict[str, Any]:
        reqs = []
        for i, doc in enumerate(documents):
            reqs.append(
                {
                    "id": f"REQ-{i+1:03d}",
                    "description": doc.get("filename", f"Requirement from doc {i+1}"),
                    "priority": "medium",
                    "type": "functional",
                    "acceptance_hints": [],
                }
            )
        return {"requirements": reqs, "features": [], "actors": ["User"], "business_rules": [], "assumptions": [], "out_of_scope": []}

    def _summarize_obligations(self, obligations: List[Dict]) -> str:
        parts = []
        for ob in obligations[:40]:
            steps = ob.get("steps") or []
            preconditions = ob.get("preconditions") or []
            expected = ob.get("expected_results") or []
            parts.append(
                f"- {ob.get('id')}: {ob.get('title')} [tier={ob.get('automation_tier')}]\n"
                f"  preconditions: {preconditions}\n"
                f"  steps: {steps[:12]}\n"
                f"  expected_results: {expected[:8]}"
            )
        return self.fit_prompt_budget("\n".join(parts))

    def _fallback_from_obligations(self, obligations: List[Dict]) -> Dict[str, Any]:
        reqs = []
        for ob in obligations:
            reqs.append(
                {
                    "id": f"REQ-{ob.get('id', 'OBL')}",
                    "description": ob.get("title", ""),
                    "priority": "high" if ob.get("automation_tier") == "automated" else "medium",
                    "type": "functional",
                    "acceptance_hints": ob.get("expected_results") or [],
                    "obligation_id": ob.get("id"),
                }
            )
        return {"requirements": reqs, "features": [], "actors": ["User"], "business_rules": [], "assumptions": [], "out_of_scope": []}
