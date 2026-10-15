"""Client intake interpreter — MCP exploration + LLM consolidation."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.automation.automation_classifier import reconcile_automation_classification
from src.intake.intake_index import build_intake_index
from src.intake.intake_mode import detect_intake_profile
from src.intake.metadata import extract_intake_metadata_from_text
from src.intake.qa_spec_chunker import (
    chunk_documents,
    obligations_from_chunks,
    summarize_chunks_for_llm,
)
from src.intake.schemas.interpreted import (
    DeliverableMeta,
    EnvironmentHints,
    InterpretedIntake,
    SourceRef,
    TestObligation,
)
from src.services.mcp_client import create_mcp_client, mcp_intake_available

logger = logging.getLogger("client_intake_interpreter")

OBLIGATION_PATTERN = re.compile(
    r"(EMPRESARIAL[-\s]?\d+|OBL[-\s]?\d+|TC[-\s]?\d+|caso\s+de\s+prueba|test\s+case)",
    re.I,
)


class ClientIntakeInterpreterAgent(BaseAgent):
    """Explore indexed intake via MCP tools and produce InterpretedIntake.

    Memory contract (scoped under run_id when orchestrated):
      Reads: context["documents"], context["source_documents"], context["metadata"],
             context["run_id"], context["project_id"]
      Writes: interpreted_intake, intake_mode
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        documents = context.get("documents") or []
        source_documents = context.get("source_documents") or documents
        metadata = context.get("metadata") or {}
        run_id = context.get("run_id", "local")
        project_id = context.get("project_id", metadata.get("project_id", "unknown"))
        profile = detect_intake_profile(documents, metadata)
        intake_mode = profile.legacy_mode

        from src.services.run_hints import get_run_hints, merge_hints_into_metadata

        metadata = merge_hints_into_metadata(metadata, run_id)
        hints = get_run_hints(run_id)
        operator_notes = list(metadata.get("operator_notes") or [])

        chunks = chunk_documents(source_documents)
        chunk_count = len(chunks)

        index = build_intake_index(project_id, documents)
        exploration: List[dict] = []
        if mcp_intake_available():
            try:
                mcp = create_mcp_client(project_id, run_id, documents)
                exploration = await mcp.explore_loop(
                    [
                        {"tool": "list_intake_files", "arguments": {}},
                        {"tool": "search_intake_text", "arguments": {"query": "pasos", "limit": 10}},
                        {"tool": "search_intake_text", "arguments": {"query": "precondicion", "limit": 10}},
                        {"tool": "list_qa_obligations", "arguments": {}},
                    ]
                )
            except Exception as exc:
                logger.warning("MCP exploration skipped: %s", exc)
        else:
            logger.info("MCP intake tools unavailable — using chunked LLM interpretation")
            exploration = [{"tool": "list_intake_files", "result": index.list_files()}]

        target_url = metadata.get("target_url") or hints.get("target_url") or ""
        if not target_url:
            for doc in source_documents + documents:
                extracted = extract_intake_metadata_from_text(doc.get("raw_text") or "")
                if extracted.get("target_url"):
                    target_url = extracted["target_url"]
                    break

        obligations = await self._extract_obligations(
            source_documents,
            documents,
            exploration,
            chunks=chunks,
            operator_notes=operator_notes,
            target_url=target_url,
        )
        deliverable_meta = self._infer_deliverable_metadata(source_documents or documents, obligations)

        interpreted = InterpretedIntake(
            intake_mode=intake_mode,
            confidence=min(0.95, profile.confidence + len(obligations) * 0.02),
            environment=EnvironmentHints(
                target_url=target_url,
                environment=metadata.get("environment", "staging"),
                modules=self._infer_modules(source_documents or documents),
            ),
            obligations=obligations,
            deliverable_metadata=deliverable_meta,
            interpretation_notes=(
                f"QA chunks: {chunk_count}; MCP steps: {len(exploration)}; obligations: {len(obligations)}"
                + (f"; operator: {' | '.join(operator_notes)}" if operator_notes else "")
            ),
        )

        interpreted_public = interpreted.public_dict()
        interpreted_public["intake_profile"] = profile.to_dict()
        self.write_memory("interpreted_intake", interpreted_public)
        self.write_memory("intake_mode", intake_mode)
        return {
            "intake_mode": intake_mode,
            "intake_profile": profile.to_dict(),
            "interpreted_intake": interpreted_public,
            "mcp_audit": exploration,
            "obligation_count": len(obligations),
            "chunk_count": chunk_count,
            "target_url": target_url,
        }

    async def _extract_obligations(
        self,
        source_documents: List[dict],
        working_documents: List[dict],
        exploration: List[dict],
        *,
        chunks: Optional[List] = None,
        operator_notes: Optional[List[str]] = None,
        target_url: str = "",
    ) -> List[TestObligation]:
        chunks = chunks or chunk_documents(source_documents or working_documents)
        parsed = obligations_from_chunks(chunks)
        fallback = self._obligations_from_dicts(parsed) or self._fallback_obligations(
            source_documents or working_documents
        )

        if not chunks and not parsed:
            doc_summary = self._legacy_doc_summary(source_documents or working_documents)
        else:
            doc_summary = summarize_chunks_for_llm(chunks)

        operator_block = ""
        if operator_notes or target_url:
            operator_block = "\nOPERATOR CONTEXT (authoritative when docs omit environment URL):\n"
            if target_url:
                operator_block += f"- target_url: {target_url}\n"
            for note in operator_notes or []:
                operator_block += f"- {note}\n"

        task = f"""TAREA: Extraer y refinar obligaciones de prueba desde intake QA empresarial (TestLink / PDF / Excel).
{operator_block}
Devuelve JSON:
{{
  "obligations": [
    {{
      "id": "OBL-EMP-13327",
      "title": string,
      "preconditions": [string],
      "steps": [string],
      "expected_results": [string],
      "automation_tier": "automated"|"manual"|"partial",
      "automation_blockers": [string],
      "automation_rationale": string,
      "required_assets": [string],
      "source_filename": string,
      "excerpt": string,
      "external_id": "EMPRESARIAL-13327"
    }}
  ]
}}

Reglas (en español en pasos y descripciones):
- Conserva ids EMPRESARIAL-xxxxx en external_id; usa OBL-EMP-xxxxx como id.
- manual → biometría, ATM, datáfono/POS, presencial, dispositivos especiales, OTP externo,
  operaciones fuera de horario no simulables, solicitudes especiales, ejecutor manual.
- automated → flujos UI reproducibles (login, inscripciones, validar, actualizar, consultas).
- partial → mayormente automatizable con verificación humana complementaria.
- Casos de días hábiles en portal empresarial → automated (calendario simulado en demo).
- NO inventes credenciales; required_assets: login_user, plain_file, etc.
- Incluye TODOS los casos de prueba distintos encontrados en los fragmentos.

OBLIGACIONES PARSEADAS (línea base determinística — refinar, no eliminar casos):
{self.compact_json(parsed[:40])}

PISTAS MCP: {self.compact_json(exploration)[:3000]}

FRAGMENTOS DE CASOS DE PRUEBA:
{self.fit_prompt_budget(doc_summary)}
"""
        result = await self.ask_json(
            task,
            {"obligations": [o.model_dump() for o in fallback]},
            temperature=0.15,
            max_tokens=self._output_budget(len(chunks) or len(fallback)),
        )
        obligations = self._parse_obligation_rows(result.get("obligations") or [], fallback)
        if len(obligations) < len(fallback):
            obligations = self._merge_obligations(fallback, obligations)
        return self._apply_classification(obligations, target_url=target_url)

    def _apply_classification(
        self, obligations: List[TestObligation], *, target_url: str = ""
    ) -> List[TestObligation]:
        from src.automation.bank_portal import is_bank_portal_context

        bank_portal = is_bank_portal_context({"target_url": target_url}, obligations=[o.model_dump() for o in obligations])
        classified: List[TestObligation] = []
        for obl in obligations:
            merged = reconcile_automation_classification(
                obl.model_dump(),
                obl.model_dump(),
                bank_portal=bank_portal,
            )
            classified.append(TestObligation.model_validate(merged))
        return classified

    def _output_budget(self, item_count: int) -> int:
        from src.agents.token_policy import output_tokens

        return output_tokens(self.agent_name, item_count=max(item_count, 8))

    def _legacy_doc_summary(self, documents: List[dict]) -> str:
        from src.agents.token_policy import intake_doc_char_limit, intake_max_documents

        parts = []
        per_doc = intake_doc_char_limit()
        for doc in documents[: intake_max_documents()]:
            text = (doc.get("raw_text") or "")[:per_doc]
            parts.append(f"FILE: {doc.get('filename')}\n{text}")
        return "\n\n".join(parts)

    def _obligations_from_dicts(self, rows: List[Dict]) -> List[TestObligation]:
        out: List[TestObligation] = []
        for raw in rows:
            out.append(
                TestObligation(
                    id=raw.get("id") or "OBL-001",
                    title=raw.get("title", ""),
                    preconditions=raw.get("preconditions") or [],
                    steps=raw.get("steps") or [],
                    expected_results=raw.get("expected_results") or [],
                    automation_tier=raw.get("automation_tier", "partial"),
                    required_assets=raw.get("required_assets") or [],
                    source_refs=[
                        SourceRef(
                            filename=raw.get("source_filename", ""),
                            excerpt=(raw.get("excerpt") or "")[:500],
                        )
                    ],
                )
            )
        return out

    def _parse_obligation_rows(
        self, rows: List[dict], fallback: List[TestObligation]
    ) -> List[TestObligation]:
        obligations: List[TestObligation] = []
        for i, raw in enumerate(rows):
            oid = raw.get("id") or (fallback[i].id if i < len(fallback) else f"OBL-{i+1:03d}")
            obligations.append(
                TestObligation(
                    id=oid,
                    title=raw.get("title", f"Obligation {i+1}"),
                    preconditions=raw.get("preconditions") or [],
                    steps=raw.get("steps") or [],
                    expected_results=raw.get("expected_results") or [],
                    automation_tier=raw.get("automation_tier", "partial"),
                    required_assets=raw.get("required_assets") or [],
                    source_refs=[
                        SourceRef(
                            filename=raw.get("source_filename", ""),
                            excerpt=(raw.get("excerpt") or "")[:500],
                        )
                    ],
                )
            )
        return obligations

    @staticmethod
    def _merge_obligations(
        baseline: List[TestObligation], refined: List[TestObligation]
    ) -> List[TestObligation]:
        by_id = {o.id: o for o in refined}
        merged: List[TestObligation] = []
        for base in baseline:
            merged.append(by_id.get(base.id, base))
        for o in refined:
            if o.id not in {b.id for b in baseline}:
                merged.append(o)
        return merged

    def _fallback_obligations(self, documents: List[dict]) -> List[TestObligation]:
        chunks = chunk_documents(documents)
        if chunks:
            return self._obligations_from_dicts(obligations_from_chunks(chunks))

        obligations: List[TestObligation] = []
        counter = 0
        for doc in documents:
            text = doc.get("raw_text") or ""
            for chunk in re.split(r"\n{2,}", text):
                if not OBLIGATION_PATTERN.search(chunk) and len(chunk) < 80:
                    continue
                if "paso" not in chunk.lower() and "step" not in chunk.lower() and len(chunk) < 200:
                    continue
                counter += 1
                obligations.append(
                    TestObligation(
                        id=f"OBL-{counter:03d}",
                        title=chunk.split("\n")[0][:120],
                        steps=[ln.strip() for ln in chunk.split("\n") if ln.strip()][:8],
                        automation_tier="partial",
                        source_refs=[SourceRef(filename=doc.get("filename", ""), excerpt=chunk[:300])],
                    )
                )
                if counter >= 32:
                    return obligations
        if not obligations and documents:
            obligations.append(
                TestObligation(
                    id="OBL-001",
                    title=f"Validate flows described in {documents[0].get('filename', 'intake')}",
                    steps=["Review primary specification and execute core UI path"],
                    automation_tier="partial",
                    source_refs=[SourceRef(filename=documents[0].get("filename", ""))],
                )
            )
        return obligations

    def _infer_deliverable_metadata(self, documents: List[dict], obligations: List[TestObligation]) -> DeliverableMeta:
        combined = "\n".join((d.get("raw_text") or "")[:5000] for d in documents)
        inc = re.search(r"INC\d+", combined, re.I)
        tec = re.search(r"TEC[-\s]?\d+", combined, re.I)
        return DeliverableMeta(
            inc_id=inc.group(0) if inc else "",
            tec_id=tec.group(0) if tec else "",
            objective="Validación funcional según especificación de pruebas del cliente",
            scope=f"{len(obligations)} obligaciones interpretadas desde intake",
        )

    def _infer_modules(self, documents: List[dict]) -> List[str]:
        modules = set()
        for doc in documents:
            text = (doc.get("raw_text") or "").lower()
            for token in ("inscripcion", "portal empresarial", "ach", "pagos"):
                if token in text:
                    modules.add(token.title())
        return sorted(modules)
