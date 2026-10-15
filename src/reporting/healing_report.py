from typing import Dict, Any


def build_healing_report(healing: Dict[str, Any]) -> Dict[str, Any]:
    healed = healing.get("healed", False)
    changes = healing.get("changes", [])
    success_rate = 1.0 if healed and changes else 0.0
    return {
        "type": "healing",
        "healed": healed,
        "changes_count": len(changes),
        "healing_success_percent": success_rate * 100,
        "automatic_recovery_percent": success_rate * 100,
        "details": healing,
    }
