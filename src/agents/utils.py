import json
from typing import Any, Dict


def parse_llm_json(response: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        start = response.find("{")
        end = response.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(response[start : end + 1])
            except json.JSONDecodeError:
                pass
    return fallback
