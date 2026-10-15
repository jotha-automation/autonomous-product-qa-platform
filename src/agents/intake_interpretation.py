"""AI agent that interprets unstructured client intake into BRD/PRD/UC/AC artifacts."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from src.agents.base import BaseAgent
from src.agents.token_policy import intake_doc_char_limit, intake_max_documents, output_tokens
from src.intake.parser import extract_sections
from src.intake.schemas.canonical import DocumentTypeEnum


MANDATORY_TYPES = ("BRD", "PRD", "UC", "AC")


class IntakeInterpretationAgent(BaseAgent):
    """
    Dedicated intake interpreter for client deliverables without clear section scaffolding.
    Produces virtual BRD, PRD, UC, and AC documents for downstream QA agents.

    Memory contract:
      Reads: context["source_documents"], context["partial_documents"], context["missing_types"], context["metadata"]
      Writes: documents (virtual BRD/PRD/UC/AC), interpretation
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        source_documents: List[dict] = context.get("source_documents") or context.get("documents") or []
        partial_documents: List[dict] = context.get("partial_documents") or source_documents
        missing_types: List[str] = context.get("missing_types") or []
        metadata: Dict[str, Any] = context.get("metadata") or {}

        source_text = self._format_sources(source_documents)
        partial_summary = self._summarize_partial(partial_documents)

        task = f"""TASK: Interpret client QA intake material and normalize it for an enterprise QA pipeline.

The source may be a single PDF, an export, meeting notes, or mixed files WITHOUT clear section headers.
You MUST infer and produce four logical artifacts even when the client did not label them explicitly:
- BRD: business objectives, stakeholders, constraints, high-level business requirements
- PRD: functional requirements, modules, user-facing behavior, URLs, integrations
- UC: actors, preconditions, main/alternate flows (numbered UC-001…)
- AC: testable Given/When/Then acceptance criteria linked to behavior

Also extract when present:
- target_url (application under test)
- project_id
- release version

Return JSON:
{{
  "interpretation_method": "explicit_sections|inferred|hybrid",
  "confidence": 0.0-1.0,
  "target_url": string|null,
  "project_id": string|null,
  "release": string|null,
  "documents": [
    {{
      "document_type": "BRD"|"PRD"|"UC"|"AC",
      "title": string,
      "raw_text": string,
      "confidence": 0.0-1.0,
      "inferred": boolean
    }}
  ],
  "notes": string
}}

Rules:
- Include ALL four document_type values when any testable information exists in the source.
- raw_text for each artifact must be substantive (not placeholders); synthesize from context when needed.
- Mark inferred=true when content was deduced rather than copied verbatim.
- Preserve IDs, URLs, actors, and test steps found in the source.
- Missing types to prioritize: {missing_types or list(MANDATORY_TYPES)}

KNOWN METADATA:
{self.compact_json(metadata)}

PARTIAL STRUCTURE ALREADY DETECTED (regex/heuristics):
{partial_summary}

SOURCE DOCUMENTS:
{source_text}
"""

        fallback = self._fallback_interpretation(source_documents, partial_documents, metadata, missing_types)
        result = await self.ask_json(
            task,
            fallback,
            temperature=0.1,
            max_tokens=output_tokens(self.agent_name, item_count=len(source_documents) * 4),
            use_fallback_on_failure=True,
        )

        documents = self._build_virtual_documents(result, source_documents)
        return {
            "documents": documents,
            "target_url": result.get("target_url") or metadata.get("target_url"),
            "project_id": result.get("project_id") or metadata.get("project_id"),
            "release": result.get("release") or metadata.get("release"),
            "interpretation": {
                "method": result.get("interpretation_method", "inferred"),
                "confidence": result.get("confidence", 0.5),
                "notes": result.get("notes", ""),
                "missing_types_requested": missing_types,
                "produced_types": sorted({d["document_type"] for d in documents}),
            },
        }

    def _format_sources(self, documents: List[dict]) -> str:
        parts: List[str] = []
        per_doc = intake_doc_char_limit()
        for index, doc in enumerate(documents[: intake_max_documents()]):
            text = (doc.get("raw_text") or "").strip()
            if not text and doc.get("sections"):
                text = "\n".join(s.get("content", "") for s in doc["sections"])
            if len(text) > per_doc:
                text = f"{text[:per_doc]}\n[document truncated at {per_doc} chars]"
            header = (
                f"--- SOURCE {index + 1}: {doc.get('filename', 'document')} "
                f"({doc.get('document_type', 'OTHER')}) ---"
            )
            parts.append(f"{header}\n{text}")
        combined = "\n\n".join(parts)
        return self.fit_prompt_budget(combined)

    @staticmethod
    def _summarize_partial(documents: List[dict]) -> str:
        if not documents:
            return "None"
        lines = [
            f"- {d.get('filename', '?')}: type={d.get('document_type', 'OTHER')}, "
            f"chars={len(d.get('raw_text') or '')}"
            for d in documents[:12]
        ]
        return "\n".join(lines)

    def _build_virtual_documents(self, result: Dict[str, Any], source_documents: List[dict]) -> List[dict]:
        base_id = source_documents[0].get("id") if len(source_documents) == 1 else "intake-multi"
        base_name = source_documents[0].get("filename", "client-intake") if source_documents else "client-intake"
        stem = base_name.rsplit(".", 1)[0] if "." in base_name else base_name
        shared = {
            "drive_file_id": source_documents[0].get("drive_file_id") if len(source_documents) == 1 else None,
            "relative_path": source_documents[0].get("relative_path") if len(source_documents) == 1 else None,
            "interpreted_by": self.agent_name,
        }

        virtual: List[dict] = []
        seen: Set[str] = set()
        for item in result.get("documents") or []:
            doc_type = str(item.get("document_type", "")).upper()
            if doc_type not in MANDATORY_TYPES or doc_type in seen:
                continue
            raw_text = (item.get("raw_text") or item.get("content") or "").strip()
            if not raw_text:
                continue
            seen.add(doc_type)
            suffix = doc_type.lower()
            virtual.append(
                {
                    **shared,
                    "id": f"{base_id}-ai-{suffix}",
                    "filename": f"{stem}-ai-{suffix}.txt",
                    "document_type": doc_type,
                    "raw_text": raw_text,
                    "sections": [
                        s.model_dump() if hasattr(s, "model_dump") else s
                        for s in extract_sections(raw_text)
                    ],
                    "metadata": {
                        "document_type": doc_type,
                        "interpretation": "ai",
                        "confidence": item.get("confidence"),
                        "inferred": item.get("inferred", True),
                        "title": item.get("title"),
                        "source_document": base_name,
                    },
                }
            )
        return virtual

    def _fallback_interpretation(
        self,
        source_documents: List[dict],
        partial_documents: List[dict],
        metadata: Dict[str, Any],
        missing_types: List[str],
    ) -> Dict[str, Any]:
        """Deterministic fallback (mock LLM / last resort): reuse partial splits or duplicate source."""
        from src.intake.metadata import extract_intake_metadata_from_text

        combined_text = "\n\n".join((d.get("raw_text") or "") for d in source_documents)
        extracted = extract_intake_metadata_from_text(combined_text)

        by_type = {
            str(d.get("document_type", "")).upper(): d
            for d in partial_documents
            if str(d.get("document_type", "")).upper() in MANDATORY_TYPES
        }

        documents_out: List[dict] = []
        for doc_type in MANDATORY_TYPES:
            if doc_type in by_type:
                chunk = by_type[doc_type].get("raw_text") or ""
                inferred = False
            else:
                chunk = combined_text[:8000] if combined_text else f"Inferred {doc_type} from client intake."
                inferred = True
            documents_out.append(
                {
                    "document_type": doc_type,
                    "title": f"{doc_type} (fallback)",
                    "raw_text": chunk,
                    "confidence": 0.35 if inferred else 0.6,
                    "inferred": inferred,
                }
            )

        return {
            "interpretation_method": "fallback",
            "confidence": 0.35,
            "target_url": extracted.get("target_url") or metadata.get("target_url"),
            "project_id": extracted.get("project_id") or metadata.get("project_id"),
            "release": extracted.get("release") or metadata.get("release"),
            "documents": documents_out,
            "notes": f"Fallback interpretation; missing types requested: {missing_types}",
        }
