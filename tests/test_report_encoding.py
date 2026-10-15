"""Regression: client-facing JSON deliverables must preserve UTF-8 accents.

Published reports are consumed directly by operators, so Spanish accents like
"Creación de inscripción" must survive serialization instead of being escaped
into literal ``\\u00`` sequences.
"""
from src.services.drive_results_publisher import collect_local_artifact_files


def test_published_reports_preserve_utf8_accents():
    summary = {
        "status": "completed",
        "results": {
            "reports": {
                "title": "Creación de inscripción",
                "steps": ["Validación de datos", "Confirmación del envío"],
            }
        },
    }

    files = collect_local_artifact_files("run-encoding-test", summary)
    by_name = {name: content for name, content, _mime in files}

    assert "reports.json" in by_name
    assert "pipeline_summary.json" in by_name

    for name, content in by_name.items():
        assert b"\\u00" not in content, f"{name} contains escaped unicode"

    reports_bytes = by_name["reports.json"]
    assert "Creación de inscripción".encode("utf-8") in reports_bytes
    assert "Validación de datos".encode("utf-8") in reports_bytes
