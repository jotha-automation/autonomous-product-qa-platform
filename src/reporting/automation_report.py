from typing import Dict, Any


def build_automation_report(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    execution = snapshot.get("execution") or {}
    return {
        "type": "automation",
        "execution_status": execution.get("status"),
        "passed": execution.get("passed", 0),
        "failed": execution.get("failed", 0),
        "artifacts": execution.get("artifacts", {}),
        "test_plan": snapshot.get("test_plan"),
    }
