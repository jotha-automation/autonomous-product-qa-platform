from typing import Dict, Any, List

from src.reporting.readiness import compute_readiness_score
from src.reporting.qa_report import build_qa_report
from src.reporting.automation_report import build_automation_report
from src.reporting.rca_report import build_rca_report
from src.reporting.healing_report import build_healing_report


def build_all_reports(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    score = compute_readiness_score(snapshot)
    executive = snapshot.get("executive") or {
        "executive_summary": "APTP pipeline report",
        "readiness_score": score,
    }
    executive["readiness_score"] = score
    return [
        build_qa_report(snapshot),
        {"type": "product", "summary": executive.get("executive_summary"), "score": score},
        executive | {"type": "executive"},
        build_automation_report(snapshot),
        build_rca_report(snapshot.get("rca") or {}),
        build_healing_report(snapshot.get("healing") or {}),
    ]
