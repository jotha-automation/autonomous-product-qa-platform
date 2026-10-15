"""Release readiness score: Coverage × Risk × RequirementQuality × Traceability."""

from typing import Any, Dict

from src.reporting.numeric import coerce_score_float


def _norm(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_readiness_score(snapshot: Dict[str, Any]) -> int:
    coverage = snapshot.get("coverage") or {}
    risk = snapshot.get("risk") or {}
    requirements = snapshot.get("requirements") or []
    matrix = snapshot.get("traceability") or []

    coverage_score = _norm(
        coerce_score_float(
            coverage.get("coverage_percent")
            or coverage.get("coverage_score")
            or 0.75
        )
    )
    risk_raw = risk.get("risk_scores", {}).get("functional", 0.2)
    risk_score = _norm(1.0 - coerce_score_float(risk_raw, 0.2))
    req_quality = _norm(min(1.0, len(requirements) / 5.0)) if requirements else 0.5
    trace_score = _norm(min(1.0, len(matrix) / max(len(requirements), 1))) if matrix else 0.5

    combined = coverage_score * risk_score * req_quality * trace_score
    return int(round(combined * 100))
