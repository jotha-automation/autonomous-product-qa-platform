from typing import Dict, Any, List, Set

from src.agents.base import BaseAgent
from src.agents.utils import parse_llm_json
from src.intake.intake_mode import detect_intake_profile
from src.intake.metadata import extract_intake_metadata_from_text
from src.intake.schemas.canonical import DocumentTypeEnum
from src.intake.schemas.interpreted import InterpretedIntake


REQUIRED_TYPES = {
    DocumentTypeEnum.BRD,
    DocumentTypeEnum.PRD,
    DocumentTypeEnum.UC,
    DocumentTypeEnum.AC,
}


class ReadinessAgent(BaseAgent):
    """Confidence-based readiness — prefers WARN + partial execution over NOT_READY.

    Memory contract:
      Reads: context["documents"], context["metadata"], interpreted_intake
      Writes: readiness_status
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        documents: List[dict] = context.get("documents") or self.read_memory("documents", [])
        metadata = context.get("metadata") or {}
        profile = detect_intake_profile(documents, metadata)
        if "interpreted_intake" in context:
            interpreted_raw = context.get("interpreted_intake")
        else:
            interpreted_raw = self.read_memory("interpreted_intake")

        if profile.primary_strategy in {"interpretation", "mixed"} or interpreted_raw:
            return self._adaptive_readiness(context, interpreted_raw, metadata, profile)

        return await self._portfolio_readiness(documents, context, profile)

    def _resolve_target_url(self, context: Dict[str, Any], metadata: dict, interpreted_raw: Any) -> str:
        url = metadata.get("target_url") or context.get("target_url", "")
        if url:
            return url
        if interpreted_raw:
            interpreted = InterpretedIntake.from_dict(interpreted_raw)
            url = (interpreted.environment.target_url or "").strip()
            if url:
                return url
        for doc in context.get("documents") or []:
            extracted = extract_intake_metadata_from_text(doc.get("raw_text") or "")
            if extracted.get("target_url"):
                return extracted["target_url"]
        return ""

    def _adaptive_readiness(
        self,
        context: Dict[str, Any],
        interpreted_raw: Any,
        metadata: dict,
        profile,
    ) -> Dict[str, Any]:
        target_url = self._resolve_target_url(context, metadata, interpreted_raw)
        obligations = []
        missing_assets = []
        gaps: List[str] = []

        if interpreted_raw:
            interpreted = InterpretedIntake.from_dict(interpreted_raw)
            obligations = interpreted.obligations
            for obl in obligations:
                if obl.required_assets and not any(
                    b.obligation_id == obl.id for b in interpreted.asset_bindings
                ):
                    if any(tok in " ".join(obl.steps).lower() for tok in ("login", "ingres", "autentic")):
                        missing_assets.append(obl.id)

        if not obligations:
            gaps.append("test_obligations")
        if not target_url:
            gaps.append("target_url")

        automatable = sum(
            1 for o in obligations if getattr(o, "automation_tier", o.get("automation_tier") if isinstance(o, dict) else "") != "manual"
        ) if obligations else 0
        if obligations and automatable == 0:
            gaps.append("no_automatable_obligations")

        block_run = "target_url" in gaps
        if profile.primary_strategy == "interpretation" and not obligations:
            block_run = True

        score = 0.35
        if obligations:
            score += min(0.4, len(obligations) * 0.03)
        if target_url:
            score += 0.2
        if profile.has_full_portfolio:
            score = max(score, 0.9)

        if gaps and score < 0.5:
            status = "WARN"
        elif gaps:
            status = "WARN"
        elif missing_assets:
            status = "WARN"
        else:
            status = "READY"

        result: Dict[str, Any] = {
            "status": status,
            "intake_mode": profile.legacy_mode,
            "intake_profile": profile.to_dict(),
            "score": round(min(0.99, score), 2),
            "gaps": gaps,
            "missing_artifacts": gaps,
            "block_run": block_run,
        }
        if obligations:
            result["obligation_count"] = len(obligations)
            result["automatable_obligations"] = automatable
            result["manual_obligations"] = [
                {
                    "id": getattr(o, "id", o.get("id") if isinstance(o, dict) else ""),
                    "title": getattr(o, "title", o.get("title") if isinstance(o, dict) else ""),
                    "blockers": getattr(o, "automation_blockers", None)
                    or (o.get("automation_blockers") if isinstance(o, dict) else []),
                    "rationale": getattr(o, "automation_rationale", None)
                    or (o.get("automation_rationale") if isinstance(o, dict) else ""),
                }
                for o in obligations
                if (getattr(o, "automation_tier", None) or (o.get("automation_tier") if isinstance(o, dict) else ""))
                == "manual"
            ]
        if missing_assets:
            result["missing_assets"] = missing_assets
        if target_url:
            result["target_url"] = target_url

        self.write_memory("readiness_status", result["status"])
        self.write_memory("missing_artifacts", result.get("missing_artifacts", []))
        return result

    async def _portfolio_readiness(
        self, documents: List[dict], context: Dict[str, Any], profile
    ) -> Dict[str, Any]:
        present: Set[str] = set()
        for doc in documents:
            dt = doc.get("document_type") or doc.get("doc_type")
            if hasattr(dt, "value"):
                dt = dt.value
            if dt:
                present.add(str(dt).upper())

        missing = [t.value for t in REQUIRED_TYPES if t.value not in present]
        score = max(0.25, 1.0 - len(missing) * 0.18)

        if missing:
            result = {
                "status": "WARN",
                "intake_mode": profile.legacy_mode,
                "intake_profile": profile.to_dict(),
                "missing_artifacts": missing,
                "present_types": sorted(present),
                "score": round(score, 2),
                "gaps": missing,
                "block_run": False,
            }
        else:
            result = {
                "status": "READY",
                "intake_mode": profile.legacy_mode,
                "intake_profile": profile.to_dict(),
                "missing_artifacts": [],
                "present_types": sorted(present),
                "score": 1.0,
                "gaps": [],
                "block_run": False,
            }

        if context.get("use_llm", False):
            response = await self.ask_llm(
                f"Validate readiness for documents: {present}",
                system_prompt="Return JSON with status READY or WARN.",
            )
            result = parse_llm_json(response, result)

        self.write_memory("readiness_status", result["status"])
        self.write_memory("missing_artifacts", result.get("missing_artifacts", []))
        return result
