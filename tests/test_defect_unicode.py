from src.agents.defect_detection import DefectDetectionAgent
from src.reporting.error_sanitization import consolidate_execution_failures


def test_coverage_gap_description_preserves_spanish_accents():
    gap = {
        "id": "REQ_001",
        "description": "Validar creación de inscripción ACH",
        "missing_scenarios": True,
        "suggested_scenario": "CP_0001_Creación_de_inscripción",
    }
    description = DefectDetectionAgent._coverage_gap_description(gap)
    assert "creación" in description
    assert "Creación" in description
    assert "\\u00f3" not in description


def test_missing_runtime_secret_collapses_to_infra_blocker():
    failures = [
        {
            "title": "OBL-EMP-12975",
            "error": {
                "message": "Error: Missing runtime secret for key: login_user.cliente (expected APTP_SECRET_LOGIN_USER_CLIENTE)"
            },
        }
        for _ in range(5)
    ]
    execution = {
        "failed": 5,
        "executed_tests": [{"error": failures[0]["error"]["message"]} for _ in range(5)],
        "failures": failures,
    }
    per_test, blocker = consolidate_execution_failures(failures, execution=execution)
    assert blocker is not None
    assert per_test == []
    assert "missing runtime secret" in blocker["description"].casefold()
