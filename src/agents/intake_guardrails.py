"""Intake guardrails agent — validates files before interpretation."""
from __future__ import annotations

from typing import Any, Dict

from src.agents.base import BaseAgent
from src.intake.guardrails import (
    GuardrailResult,
    format_violation_messages,
    validate_documents_for_pipeline,
)


class IntakeGuardrailsAgent(BaseAgent):
    """Pre-pipeline guardrails: file types, content relevance, credential routing flags.

    Memory contract:
      Reads: context["documents"]
      Writes: guardrail_result, pii_flagged_paths
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        documents = context.get("documents") or []
        result: GuardrailResult = validate_documents_for_pipeline(documents)

        self.write_memory("guardrail_result", result.to_dict())
        if result.pii_flagged_paths:
            self.write_memory("pii_flagged_paths", result.pii_flagged_paths)

        payload = {
            "accepted": result.accepted,
            "guardrails": result.to_dict(),
            "qa_relevant_count": result.qa_relevant_count,
            "pii_flagged_paths": result.pii_flagged_paths,
        }

        if not result.accepted:
            payload["status"] = "REJECTED"
            payload["message"] = format_violation_messages(result)
        else:
            payload["status"] = "PASSED"
            if result.warnings:
                payload["warnings"] = [w.__dict__ for w in result.warnings]

        return payload
