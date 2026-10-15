"""Tests for evidence markdown report generation."""
from pathlib import Path

from src.reporting.evidence_markdown_report import build_evidence_markdown_report


def test_build_evidence_markdown_report(tmp_path):
    artifact_dir = tmp_path / "aptp-artifacts" / "run-md"
    artifact_dir.mkdir(parents=True)
    evidence_dir = artifact_dir / "evidence" / "screenshots" / "TC-001"
    evidence_dir.mkdir(parents=True)
    screenshot = evidence_dir / "step-validar-resultado.png"
    screenshot.write_bytes(b"png")

    execution = {
        "status": "failed",
        "executed_tests": [
            {
                "id": "TC-001",
                "title": "EMPRESARIAL-13330: Crear inscripción",
                "status": "failed",
                "duration_ms": 1200,
                "error": {"message": "expect(locator).toBeVisible() failed"},
                "evidence_refs": [
                    {
                        "screenshot_path": "screenshots/TC-001/step-validar-resultado.png",
                        "label": "Validar resultado final",
                        "media_type": "screenshot",
                    }
                ],
            }
        ],
    }
    manifest = {
        "entries": [
            {
                "test_id": "TC-001",
                "test_title": "EMPRESARIAL-13330: Crear inscripción",
                "path": "screenshots/TC-001/step-validar-resultado.png",
                "label": "Validar resultado final",
                "media_type": "screenshot",
            }
        ],
        "screenshot_count": 1,
    }

    dest = artifact_dir / "evidence-report.md"
    result = build_evidence_markdown_report(
        dest,
        run_id="run-md",
        project_id="DEMO-QA",
        execution_results=execution,
        manifest=manifest,
    )
    assert result["sections"] == 1
    text = dest.read_text(encoding="utf-8")
    assert "# Informe de evidencias — run-md" in text
    assert "EMPRESARIAL-13330" in text
    assert "![Validar resultado final]" in text
    assert "screenshots/TC-001/step-validar-resultado.png" in text
