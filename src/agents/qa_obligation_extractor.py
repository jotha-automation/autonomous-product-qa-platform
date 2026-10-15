"""Refine QA obligations from primary specification documents."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.automation.automation_classifier import apply_automation_classification, reconcile_automation_classification
from src.intake.qa_spec_chunker import chunk_documents, obligations_from_chunks, summarize_chunks_for_llm
from src.intake.schemas.interpreted import InterpretedIntake, TestObligation

logger = logging.getLogger("qa_obligation_extractor")

_LLM_BATCH_SIZE = 10


class QaObligationExtractorAgent(BaseAgent):
    """Refine QA obligations and automation classification.

    Memory contract:
      Reads: interpreted_intake (obligations), context["documents"]
      Writes: interpreted_intake (refined obligations)
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        interpreted_raw = context.get("interpreted_intake") or self.read_memory("interpreted_intake")
        if not interpreted_raw:
            return {"obligations": [], "skipped": True}

        interpreted = InterpretedIntake.from_dict(interpreted_raw)
        documents = context.get("documents") or []
        primary = [
            d for d in documents
            if (d.get("extra") or {}).get("intake_role") == "PRIMARY_SPEC"
        ] or documents[:2]

        refined = await self._refine_obligations(interpreted.obligations, primary, context)
        interpreted.obligations = refined
        if interpreted.deliverable_metadata:
            automated = sum(1 for o in refined if o.automation_tier == "automated")
            manual = sum(1 for o in refined if o.automation_tier == "manual")
            interpreted.deliverable_metadata.scope = (
                f"{len(refined)} obligaciones — {automated} automatizables, {manual} manuales"
            )
        self.write_memory("interpreted_intake", interpreted.to_dict())

        manual_details = [
            {
                "id": o.id,
                "title": o.title,
                "blockers": o.automation_blockers,
                "rationale": o.automation_rationale,
            }
            for o in refined
            if o.automation_tier == "manual"
        ]

        return {
            "obligations": [o.model_dump() for o in refined],
            "interpreted_intake": interpreted.public_dict(),
            "count": len(refined),
            "automated": sum(1 for o in refined if o.automation_tier == "automated"),
            "partial": sum(1 for o in refined if o.automation_tier == "partial"),
            "manual": sum(1 for o in refined if o.automation_tier == "manual"),
            "manual_obligations": manual_details,
            "automatable_obligations": [
                o.model_dump() for o in refined if o.automation_tier != "manual"
            ],
        }

    async def _refine_obligations(
        self, obligations: List[TestObligation], documents: List[dict], context: Dict[str, Any]
    ) -> List[TestObligation]:
        if not obligations:
            chunks = chunk_documents(documents)
            if chunks:
                from src.agents.client_intake_interpreter import ClientIntakeInterpreterAgent

                parsed = ClientIntakeInterpreterAgent(self.memory)._obligations_from_dicts(
                    obligations_from_chunks(chunks)
                )
                if parsed:
                    obligations = parsed

        if not obligations:
            return obligations

        baseline = [self._with_heuristics(o, context) for o in obligations]
        patches: List[TestObligation] = []
        for start in range(0, len(obligations), _LLM_BATCH_SIZE):
            batch = obligations[start : start + _LLM_BATCH_SIZE]
            patches.extend(await self._llm_refine_batch(batch, documents, context))
        return self._merge_refined_obligations(baseline, patches)

    async def _llm_refine_batch(
        self, batch: List[TestObligation], documents: List[dict], context: Dict[str, Any]
    ) -> List[TestObligation]:
        chunks = chunk_documents(documents)
        if chunks:
            doc_text = summarize_chunks_for_llm(chunks, max_chunks=8, per_chunk=1200)
        else:
            from src.agents.token_policy import intake_doc_char_limit, intake_max_documents

            per_doc = intake_doc_char_limit()
            doc_text = "\n".join(
                (d.get("raw_text") or "")[:per_doc]
                for d in documents[: intake_max_documents()]
            )
            doc_text = self.fit_prompt_budget(doc_text)

        count = len(batch)
        task = f"""TAREA: Clasificar automation_tier de cada obligación de prueba empresarial.

Devuelve JSON: {{ "obligations": [ ... ] }}

CRÍTICO:
- Devuelve EXACTAMENTE {count} obligaciones — una fila de salida por fila de entrada, mismo orden.
- Conserva cada id de obligación exactamente como se proporciona.
- automation_tier: automated | partial | manual.
- Incluye automation_blockers (string[]) y automation_rationale en español cuando tier sea manual o partial.

Reglas de clasificación:
- manual → biometría, cajeros ATM, datáfonos/POS, pasos presenciales en sucursal, dispositivos físicos,
  operaciones solo válidas en horarios/días fuera de simulación del portal, OTP/SMS externo,
  solicitudes especiales, procesos que no pueden ejecutarse de forma continua en E2E web.
- automated → flujos UI/API reproducibles en la aplicación objetivo (login, formularios, consultas, ACH).
- partial → mayormente automatizable pero requiere verificación humana, aprobaciones o notificaciones externas.
- Casos de días hábiles en portal empresarial demo → automated (calendario simulado).

OBLIGACIONES DE ENTRADA ({count}): {self.compact_json([o.model_dump() for o in batch])}
EXTRACTO DOCUMENTO PRIMARIO: {self.fit_prompt_budget(doc_text)}
"""
        fallback = {"obligations": [self._apply_heuristics(o, context) for o in batch]}
        result = await self.ask_json(task, fallback, temperature=0.1)
        return self._parse_refined_rows(batch, result.get("obligations") or [], context)

    def _parse_refined_rows(
        self, batch: List[TestObligation], rows: List[Any], context: Dict[str, Any]
    ) -> List[TestObligation]:
        by_id = {
            str(raw.get("id")): raw
            for raw in rows
            if isinstance(raw, dict) and raw.get("id")
        }
        refined: List[TestObligation] = []
        for i, obl in enumerate(batch):
            raw = by_id.get(obl.id)
            if raw is None and i < len(rows) and isinstance(rows[i], dict):
                raw = rows[i]
            if isinstance(raw, dict):
                refined.append(self._patch_from_raw(obl, raw, context))
            else:
                refined.append(self._with_heuristics(obl, context))
        if len(refined) < len(batch):
            logger.warning(
                "LLM returned %s obligations for batch of %s — preserving baseline rows",
                len(refined),
                len(batch),
            )
            seen = {o.id for o in refined}
            for obl in batch:
                if obl.id not in seen:
                    refined.append(self._with_heuristics(obl, context))
        return refined[: len(batch)]

    def _merge_refined_obligations(
        self, baseline: List[TestObligation], patches: List[TestObligation]
    ) -> List[TestObligation]:
        by_id = {o.id: o for o in patches}
        merged: List[TestObligation] = []
        for base in baseline:
            merged.append(by_id.get(base.id, base))
        if len(merged) < len(baseline):
            logger.warning(
                "Obligation refinement dropped rows (%s -> %s) — restoring baseline",
                len(baseline),
                len(merged),
            )
            return baseline
        return merged

    def _patch_from_raw(self, obl: TestObligation, raw: Dict[str, Any], context: Dict[str, Any]) -> TestObligation:
        patched = TestObligation.model_validate(
            {
                **obl.model_dump(),
                "title": raw.get("title") or obl.title,
                "steps": raw.get("steps") or obl.steps,
                "preconditions": raw.get("preconditions") or obl.preconditions,
                "expected_results": raw.get("expected_results") or obl.expected_results,
            }
        )
        from src.automation.bank_portal import is_bank_portal_context

        bank_portal = is_bank_portal_context(context, obligations=[patched.model_dump()])
        merged = reconcile_automation_classification(
            patched.model_dump(),
            raw,
            bank_portal=bank_portal,
        )
        return TestObligation.model_validate(merged)

    def _with_heuristics(self, obl: TestObligation, context: Dict[str, Any]) -> TestObligation:
        data = obl.model_dump()
        data.update(self._classification_fields(obl, context))
        return TestObligation.model_validate(data)

    def _apply_heuristics(self, obl: TestObligation, context: Dict[str, Any]) -> dict:
        data = obl.model_dump()
        data.update(self._classification_fields(obl, context))
        return data

    def _classification_fields(self, obl: TestObligation, context: Dict[str, Any]) -> Dict[str, Any]:
        from src.automation.bank_portal import is_bank_portal_context

        bank_portal = is_bank_portal_context(context, obligations=[obl.model_dump()])
        classified = apply_automation_classification(obl.model_dump(), bank_portal=bank_portal)
        return {
            "automation_tier": classified.get("automation_tier", "partial"),
            "automation_blockers": classified.get("automation_blockers") or [],
            "automation_rationale": classified.get("automation_rationale") or "",
        }
