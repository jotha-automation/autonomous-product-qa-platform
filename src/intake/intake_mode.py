"""Detect intake profile from documents — no env toggles."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Set

from src.intake.schemas.canonical import DocumentTypeEnum

MANDATORY_TYPES = {DocumentTypeEnum.BRD, DocumentTypeEnum.PRD, DocumentTypeEnum.UC, DocumentTypeEnum.AC}
MANDATORY_VALUES = {t.value for t in MANDATORY_TYPES}

PORTFOLIO_TEXT_HINTS = (
    "acceptance criteria",
    "acceptance",
    "use case",
    "uc-",
    "business requirement",
    "product requirement",
    "brd",
    "prd",
)

QA_RELEVANCE_HINTS = (
    "test",
    "qa",
    "quality",
    "requirement",
    "acceptance",
    "use case",
    "scenario",
    "paso",
    "prueba",
    "validaci",
    "oblig",
    "caso de prueba",
    "precondicion",
    "expected result",
    "wireframe",
    "staging",
    "portal",
    "login",
    "http",
)

Strategy = Literal["portfolio", "interpretation", "mixed"]


@dataclass
class IntakeProfile:
    """Signals derived from intake documents — drives adaptive pipeline routing."""

    present_mandatory: Set[str] = field(default_factory=set)
    missing_mandatory: List[str] = field(default_factory=list)
    has_portfolio_structure: bool = False
    has_unstructured_content: bool = False
    is_mixed: bool = False
    primary_strategy: Strategy = "interpretation"
    confidence: float = 0.5
    document_count: int = 0
    signals: Dict[str, bool] = field(default_factory=dict)

    @property
    def has_full_portfolio(self) -> bool:
        return not self.missing_mandatory

    @property
    def legacy_mode(self) -> str:
        """Backward-compatible label for reports/UI."""
        if self.primary_strategy == "portfolio" and self.has_full_portfolio:
            return "portfolio_legacy"
        return "content_first"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "present_mandatory": sorted(self.present_mandatory),
            "missing_mandatory": self.missing_mandatory,
            "has_portfolio_structure": self.has_portfolio_structure,
            "has_unstructured_content": self.has_unstructured_content,
            "is_mixed": self.is_mixed,
            "primary_strategy": self.primary_strategy,
            "confidence": self.confidence,
            "document_count": self.document_count,
            "signals": self.signals,
            "intake_mode": self.legacy_mode,
        }


def present_mandatory_types(documents: List[dict]) -> Set[str]:
    present: Set[str] = set()
    for doc in documents:
        dt = doc.get("document_type") or doc.get("doc_type")
        if hasattr(dt, "value"):
            dt = dt.value
        if dt:
            present.add(str(dt).upper())
    return present


def _text_has_portfolio_hints(text: str) -> bool:
    lower = text[:8000].lower()
    return any(tok in lower for tok in PORTFOLIO_TEXT_HINTS)


def _doc_looks_unstructured(doc: dict) -> bool:
    dtype = str(doc.get("document_type", "")).upper()
    if dtype in MANDATORY_VALUES:
        return False
    text = (doc.get("raw_text") or "")[:4000]
    name = (doc.get("filename") or "").lower()
    if dtype == "OTHER" and len(text) > 200:
        return True
    if any(ext in name for ext in (".pdf", ".xlsx", ".xls")) and dtype not in MANDATORY_VALUES:
        if "prueba" in name or "spec" in name or "intake" in name or "entregable" in name:
            return True
    return False


def detect_intake_profile(
    documents: List[dict],
    metadata: Dict[str, Any] | None = None,
) -> IntakeProfile:
    """
    Infer how to process intake from document signals — never reads env vars.
    """
    metadata = metadata or {}
    present = present_mandatory_types(documents)
    missing = [t.value for t in MANDATORY_TYPES if t.value not in present]

    has_portfolio = bool(present & MANDATORY_VALUES)
    unstructured_count = sum(1 for d in documents if _doc_looks_unstructured(d))

    if not has_portfolio and documents:
        for doc in documents:
            if _text_has_portfolio_hints(doc.get("raw_text") or ""):
                has_portfolio = True
                break
            inferred = str(doc.get("inferred_doc_type", "")).upper()
            if inferred in MANDATORY_VALUES:
                has_portfolio = True
                present.add(inferred)
                break

    if present:
        missing = [t.value for t in MANDATORY_TYPES if t.value not in present]

    has_unstructured = unstructured_count > 0 or (
        len(documents) == 1 and not (present & MANDATORY_VALUES)
    )
    is_mixed = has_portfolio and has_unstructured and len(documents) > 1

    if is_mixed:
        strategy: Strategy = "mixed"
    elif has_portfolio and len(missing) <= 2:
        strategy = "portfolio"
    elif has_portfolio and missing:
        strategy = "mixed"
    else:
        strategy = "interpretation"

    confidence = 0.35
    if present & MANDATORY_VALUES:
        confidence += 0.15 * len(present & MANDATORY_VALUES)
    if not missing:
        confidence = min(0.98, confidence + 0.25)
    if has_unstructured and strategy == "interpretation":
        confidence = max(confidence, 0.55)
    if is_mixed:
        confidence = max(confidence, 0.6)

    meta_mode = metadata.get("intake_mode")
    if meta_mode in {"content_first", "portfolio_legacy"} and not documents:
        strategy = "portfolio" if meta_mode == "portfolio_legacy" else "interpretation"

    return IntakeProfile(
        present_mandatory=present,
        missing_mandatory=missing,
        has_portfolio_structure=has_portfolio,
        has_unstructured_content=has_unstructured,
        is_mixed=is_mixed,
        primary_strategy=strategy,
        confidence=min(0.99, confidence),
        document_count=len(documents),
        signals={
            "has_brd": "BRD" in present,
            "has_prd": "PRD" in present,
            "has_uc": "UC" in present,
            "has_ac": "AC" in present,
            "has_unstructured_docs": has_unstructured,
            "is_mixed": is_mixed,
        },
    )


def intake_mode_from_context(metadata: Dict[str, Any], documents: List[dict]) -> str:
    """Legacy helper — returns report-friendly mode label from detected profile."""
    return detect_intake_profile(documents, metadata).legacy_mode
