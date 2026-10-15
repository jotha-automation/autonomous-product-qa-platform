"""Intake normalization: adaptive regex + AI interpretation (no env toggles)."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from src.intake.combined import expand_combined_intake_documents
from src.intake.intake_mode import detect_intake_profile, present_mandatory_types
from src.intake.schemas.canonical import DocumentTypeEnum

if TYPE_CHECKING:
    from src.agents.intake_interpretation import IntakeInterpretationAgent
    from src.agents.client_intake_interpreter import ClientIntakeInterpreterAgent

MANDATORY = (
    DocumentTypeEnum.BRD,
    DocumentTypeEnum.PRD,
    DocumentTypeEnum.UC,
    DocumentTypeEnum.AC,
)


def missing_mandatory_types(documents: List[dict]) -> List[DocumentTypeEnum]:
    present = present_mandatory_types(documents)
    return [t for t in MANDATORY if t.value not in present]


def _is_qa_spec_intake(documents: List[dict]) -> bool:
    """QA/TestLink specs should not trigger portfolio (BRD/PRD/UC/AC) fabrication."""
    from src.intake.qa_spec_chunker import is_qa_spec_text

    for doc in documents:
        if is_qa_spec_text(doc.get("raw_text") or "", doc.get("filename", "")):
            return True
    return False


def needs_ai_interpretation(documents: List[dict], *, after_regex: List[dict]) -> bool:
    """Use AI when mandatory portfolio types are still missing after heuristics.

    Skip portfolio fabrication for QA/TestLink specs; those go directly to
    ClientIntakeInterpreterAgent for obligation extraction.
    """
    if _is_qa_spec_intake(documents):
        return False
    if not after_regex:
        return bool(documents)
    if not missing_mandatory_types(after_regex):
        return False
    return True


def needs_content_interpretation(profile, documents: List[dict], working_docs: List[dict]) -> bool:
    """Whether to run obligation interpretation (chunked QA specs, unstructured intake)."""
    from src.intake.qa_spec_chunker import chunk_documents, is_qa_spec_text

    if chunk_documents(documents):
        return True
    for doc in documents:
        if is_qa_spec_text(doc.get("raw_text") or "", doc.get("filename", "")):
            return True
    if not missing_mandatory_types(working_docs):
        return False
    if profile.primary_strategy in {"interpretation", "mixed"}:
        return True
    if profile.has_unstructured_content:
        return True
    if not profile.has_full_portfolio and documents:
        return True
    return False


def merge_document_sets(primary: List[dict], secondary: List[dict]) -> List[dict]:
    """Prefer primary per type; fill gaps from secondary (AI)."""
    merged: Dict[str, dict] = {}
    for doc in primary + secondary:
        dt = str(doc.get("document_type", "")).upper()
        if dt in {t.value for t in MANDATORY} and dt not in merged:
            merged[dt] = doc
    extras = [
        d
        for d in primary + secondary
        if str(d.get("document_type", "")).upper() not in merged
    ]
    return list(merged.values()) + extras


async def _run_intake_interpretation(
    agent: Optional["IntakeInterpretationAgent"],
    documents: List[dict],
    working_docs: List[dict],
    missing_types: List[str],
    metadata: Optional[Dict[str, Any]],
    execute_agent: Optional[Callable[[Any, Dict[str, Any]], Awaitable[Dict[str, Any]]]],
) -> tuple[List[dict], Dict[str, Any]]:
    if agent is None:
        from src.agents.intake_interpretation import IntakeInterpretationAgent

        agent = IntakeInterpretationAgent()

    agent_context = {
        "source_documents": documents,
        "partial_documents": working_docs,
        "missing_types": missing_types,
        "metadata": metadata or {},
    }
    if execute_agent:
        ai_result = await execute_agent(agent, agent_context)
    else:
        ai_result = await agent.execute(agent_context)

    ai_docs = ai_result.get("documents") or []
    merged = merge_document_sets(working_docs, ai_docs)
    side_meta = {
        "interpretation": ai_result.get("interpretation", {}),
        "target_url": ai_result.get("target_url"),
        "project_id": ai_result.get("project_id"),
        "release": ai_result.get("release"),
    }
    return merged, side_meta


async def normalize_intake_documents(
    documents: List[dict],
    agent: Optional["IntakeInterpretationAgent"] = None,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    execute_agent: Optional[Callable[[Any, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
    intake_mode_override: Optional[str] = None,
    memory: Optional[Any] = None,
    run_id: Optional[str] = None,
) -> Tuple[List[dict], Dict[str, Any]]:
    """
    Unified adaptive intake normalization:
    - Always attempt combined PDF split when applicable
    - Always invoke IntakeInterpretationAgent when BRD/PRD/UC/AC are missing
    - Unstructured / QA specs → ClientIntakeInterpreter (chunked obligations)
    """
    profile = detect_intake_profile(documents, metadata or {})
    meta: Dict[str, Any] = {
        "regex_expanded": False,
        "method": "unchanged",
        "ai_invoked": False,
        "intake_profile": profile.to_dict(),
        "intake_mode": profile.legacy_mode,
        "missing_before": profile.missing_mandatory,
    }
    if not documents:
        return documents, meta

    working_docs = list(documents)
    interpreted_intake = None

    expanded = expand_combined_intake_documents(documents)
    if len(expanded) != len(documents):
        meta["regex_expanded"] = True
        working_docs = expanded
        meta["method"] = "regex_split"

    meta["missing_after_regex"] = [t.value for t in missing_mandatory_types(working_docs)]

    if needs_ai_interpretation(documents, after_regex=working_docs):
        working_docs, side = await _run_intake_interpretation(
            agent,
            documents,
            working_docs,
            meta["missing_after_regex"],
            metadata,
            execute_agent,
        )
        meta.update(
            {
                "method": "ai_interpretation" if meta["method"] == "unchanged" else f"{meta['method']}+ai_interpretation",
                "ai_invoked": True,
                "interpretation": side.get("interpretation", {}),
                "missing_after": [t.value for t in missing_mandatory_types(working_docs)],
            }
        )
        for key in ("target_url", "project_id", "release"):
            if side.get(key):
                meta[key] = side[key]
    elif not meta["regex_expanded"]:
        meta["method"] = "native_types"
        meta["missing_after"] = meta["missing_after_regex"]

    if needs_content_interpretation(profile, documents, working_docs):
        from src.agents.client_intake_interpreter import ClientIntakeInterpreterAgent

        interpreter = ClientIntakeInterpreterAgent(memory=memory)
        if run_id:
            interpreter.set_memory_scope(f"run:{run_id}")
        ctx = {
            "documents": working_docs,
            "source_documents": documents,
            "metadata": metadata or {},
            "intake_profile": profile.to_dict(),
        }
        if execute_agent:
            ai_result = await execute_agent(interpreter, ctx)
        else:
            ai_result = await interpreter.execute(ctx)

        if ai_result.get("interpreted_intake"):
            interpreted_intake = ai_result.get("interpreted_intake")
            meta["interpreted_intake"] = interpreted_intake
            meta["ai_invoked"] = True
            if meta["method"] == "unchanged":
                meta["method"] = "content_interpretation"
            elif "content_interpretation" not in meta["method"]:
                meta["method"] = f"{meta['method']}+content_interpretation"
            meta["interpretation"] = ai_result.get("exploration", meta.get("interpretation", {}))
            meta["chunk_count"] = ai_result.get("chunk_count", 0)
            if ai_result.get("target_url"):
                meta["target_url"] = ai_result["target_url"]

    if interpreted_intake:
        meta["interpreted_intake"] = interpreted_intake

    return working_docs, meta
