from typing import Dict, Any


def build_qa_report(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "technical_qa",
        "coverage": snapshot.get("coverage", {}),
        "requirements_count": len(snapshot.get("requirements", [])),
        "scenarios_count": len(snapshot.get("scenarios", [])),
        "traceability_rows": len(snapshot.get("traceability", [])),
    }
