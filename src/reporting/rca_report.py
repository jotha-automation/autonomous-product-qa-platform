from typing import Dict, Any


def build_rca_report(rca: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "rca", **rca}
