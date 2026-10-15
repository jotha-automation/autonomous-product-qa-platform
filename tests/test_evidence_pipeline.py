import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from src.agents.playwright_architect import PlaywrightArchitectAgent
from src.agents.playwright_generator import PlaywrightGeneratorAgent
from src.memory.enhanced_memory import get_memory
from src.reporting.pdf_execution_report import build_execution_pdf
from src.services.artifact_bundle import (
    build_run_artifact_zip,
    snapshot_generated_scripts,
    write_run_reports_to_disk,
)
from src.services.evidence_collector import collect_and_persist_evidence


def test_normalize_scenario_adds_capture_evidence():
    agent = PlaywrightArchitectAgent.__new__(PlaywrightArchitectAgent)
    scenario = agent._normalize_scenario(
        {
            "id": "E2E-001",
            "steps": [
                {"action": "goto", "target": "BASE_URL", "data": "https://example.com"},
                {"action": "click", "target": "#btn"},
            ],
        }
    )
    assert scenario["steps"][0]["capture_evidence"] is True
    assert scenario["steps"][0]["id"].startswith("step-")
    assert "step-01-goto" in scenario["evidence_steps"]


def test_generate_spec_includes_capture_step_evidence():
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    spec = agent._generate_spec(
        {
            "id": "FS-009",
            "name": "Contact form validates input",
            "tags": ["smoke"],
            "steps": [
                {
                    "id": "step-01-goto",
                    "action": "goto",
                    "target": "BASE_URL",
                    "data": "https://example.com",
                    "capture_evidence": True,
                }
            ],
            "assertions": [{"type": "visible", "target": "body", "expected": "true"}],
        },
        "ContactSection",
        "contact-form",
        {},
        "https://example.com",
    )
    assert "async ({ page }, testInfo) =>" in spec
    assert "captureStepEvidence" in spec
    assert "fixtures/evidence" in spec


def test_snapshot_generated_scripts(tmp_path):
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "smoke.spec.ts").write_text("test('x', async () => {});", encoding="utf-8")
    (project / "playwright.config.ts").write_text("export default {};", encoding="utf-8")

    dest = snapshot_generated_scripts("run-abc", project, {"files": ["tests/smoke.spec.ts"]})
    assert (dest / "tests" / "smoke.spec.ts").is_file()
    assert (dest / "playwright.config.ts").is_file()
    assert (dest / "generated-inventory.json").is_file()


def test_safe_segment_strips_accents_like_playwright():
    from src.services.evidence_collector import _safe_segment

    title = "CP-0001 PARÁMETRO 1 DÍA HÁBIL"
    assert _safe_segment(title) == "CP-0001-PAR-METRO-1-D-A-H-BIL"


def test_scan_evidence_uses_annotation_labels(tmp_path):
    from src.services.evidence_collector import _labels_from_results_json, _safe_segment, _scan_project_evidence

    title = "OBL-EMP-13330: Crear inscripcion digitar"
    test_key = _safe_segment(title)
    evidence = tmp_path / "evidence" / test_key
    evidence.mkdir(parents=True)
    (evidence / "step-01-login.png").write_bytes(b"png")

    report = {
        "suites": [
            {
                "specs": [
                    {
                        "title": title,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "annotations": [
                                            {
                                                "type": "evidence",
                                                "description": '{"stepId":"step-01-login","label":"Bank portal login"}',
                                            }
                                        ]
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        ]
    }
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "results.json").write_text(__import__("json").dumps(report), encoding="utf-8")

    labels = _labels_from_results_json(tmp_path)
    assert "step-01-login" in list(labels.values())[0]
    entries = _scan_project_evidence(tmp_path)
    assert entries[0]["label"] == "Bank portal login"


def test_scan_evidence_includes_videos(tmp_path):
    from src.services.evidence_collector import _safe_segment, _scan_project_evidence

    title = "EMPRESARIAL-13330: Crear inscripcion digitar"
    test_key = _safe_segment(title)
    evidence = tmp_path / "evidence" / test_key
    evidence.mkdir(parents=True)
    (evidence / "step-01-login.png").write_bytes(b"png")
    (evidence / "run.webm").write_bytes(b"webm")

    entries = _scan_project_evidence(tmp_path)
    media = {e["step_id"]: e["media_type"] for e in entries}
    assert media["step-01-login"] == "screenshot"
    assert media["run"] == "video"


def test_collect_live_evidence_copies_videos(tmp_path):
    from src.services.evidence_collector import _safe_segment

    title = "EMPRESARIAL-13330: Crear inscripcion digitar"
    test_key = _safe_segment(title)
    evidence = tmp_path / "evidence" / test_key
    evidence.mkdir(parents=True)
    (evidence / "run.webm").write_bytes(b"webm-bytes")

    exec_result = {
        "mode": "live",
        "passed": 1,
        "failed": 0,
        "total": 1,
        "pass_rate": 1.0,
        "duration_seconds": 1.0,
        "executed_tests": [{"id": "TC-001", "title": title, "status": "passed", "duration_ms": 100}],
        "failures": [],
        "artifacts": {"screenshots": [], "traces": [], "videos": []},
    }
    result = collect_and_persist_evidence(
        "run-video",
        tmp_path,
        exec_result,
        project_id="demo",
        target_url="https://example.com",
    )
    manifest = json.loads(
        (tmp_path / "aptp-artifacts" / "run-video" / "evidence" / "evidence-manifest.json").read_text()
    )
    assert manifest["video_count"] == 1
    assert (tmp_path / "aptp-artifacts" / "run-video" / "evidence" / "videos" / test_key / "run.webm").is_file()
    assert result["screenshot_count"] == 0


def test_scaffold_includes_video_reporter(tmp_path):
    agent = PlaywrightGeneratorAgent.__new__(PlaywrightGeneratorAgent)
    written = agent._scaffold_minimal_boilerplate(tmp_path, "demo", "https://example.com", "")
    assert "fixtures/video-evidence-reporter.ts" in written
    config = (tmp_path / "playwright.config.ts").read_text(encoding="utf-8")
    assert "video-evidence-reporter" in config
    assert "video: 'on'" in config


def test_collect_mock_evidence_and_pdf(tmp_path):
    exec_result = {
        "mode": "mock",
        "passed": 1,
        "failed": 0,
        "total": 1,
        "pass_rate": 1.0,
        "duration_seconds": 1.0,
        "executed_tests": [{"id": "TC-001", "title": "smoke", "status": "passed", "duration_ms": 100}],
        "failures": [],
        "artifacts": {"screenshots": [], "traces": [], "videos": []},
    }
    result = collect_and_persist_evidence(
        "run-mock",
        tmp_path,
        exec_result,
        project_id="demo",
        target_url="https://example.com",
    )
    assert result["screenshot_count"] == 1
    pdf = Path(result["pdf_path"])
    assert pdf.is_file()
    assert pdf.stat().st_size > 0
    md_path = Path(result["markdown_report_path"])
    assert md_path.is_file()
    assert "# Informe de evidencias" in md_path.read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "aptp-artifacts" / "run-mock" / "evidence" / "evidence-manifest.json").read_text())
    assert manifest["mode"] == "mock"
    assert exec_result["executed_tests"][0].get("evidence_refs")


def test_collect_evidence_writes_manifest_when_no_tests(tmp_path):
    exec_result = {
        "mode": "mock",
        "passed": 0,
        "failed": 0,
        "total": 0,
        "pass_rate": 0.0,
        "duration_seconds": 0.0,
        "executed_tests": [],
        "failures": [],
        "artifacts": {"screenshots": [], "traces": [], "videos": []},
    }
    result = collect_and_persist_evidence("run-empty", tmp_path, exec_result, project_id="demo")
    manifest_path = tmp_path / "aptp-artifacts" / "run-empty" / "evidence" / "evidence-manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-empty"
    assert result["manifest_path"] == str(manifest_path)


def test_build_execution_pdf(tmp_path):
    evidence_root = tmp_path / "evidence"
    img_dir = evidence_root / "screenshots" / "TC-001"
    img_dir.mkdir(parents=True)
    try:
        from PIL import Image

        Image.new("RGB", (100, 80), color=(10, 20, 30)).save(img_dir / "step-01.png")
    except Exception:
        pytest.skip("Pillow unavailable")

    manifest = {
        "entries": [
            {
                "test_id": "TC-001",
                "step_id": "step-01",
                "label": "goto",
                "path": "screenshots/TC-001/step-01.png",
            }
        ]
    }
    pdf_path = tmp_path / "execution-report.pdf"
    build_execution_pdf(
        pdf_path,
        run_id="run-1",
        project_id="demo",
        target_url="https://example.com",
        exec_result={
            "mode": "live",
            "passed": 1,
            "failed": 0,
            "total": 1,
            "pass_rate": 1.0,
            "duration_seconds": 2.5,
            "executed_tests": [{"id": "TC-001", "title": "smoke", "status": "passed", "duration_ms": 2500}],
            "failures": [],
        },
        manifest=manifest,
        evidence_root=evidence_root,
    )
    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 100


def test_collect_compile_failure_evidence_counts(tmp_path):
    exec_result = {
        "mode": "live",
        "status": "failed",
        "passed": 0,
        "failed": 3,
        "total": 0,
        "pass_rate": 0.0,
        "duration_seconds": 0.03,
        "executed_tests": [],
        "compile_errors": 3,
        "failures": [
            {
                "title": "inscripciones/crear.spec.ts",
                "error": {"message": "SyntaxError: Identifier 'captureStepEvidence' has already been declared", "category": "compile"},
            }
        ],
        "stderr": "SyntaxError: Identifier 'captureStepEvidence' has already been declared",
        "artifacts": {"screenshots": [], "traces": [], "videos": []},
    }
    result = collect_and_persist_evidence(
        "run-compile-fail",
        tmp_path,
        exec_result,
        project_id="demo",
        target_url="https://example.com",
    )
    manifest_path = tmp_path / "aptp-artifacts" / "run-compile-fail" / "evidence" / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["screenshot_count"] == 0
    assert manifest["diagnostic_count"] >= 1
    assert manifest["execution_status"] == "failed"
    assert manifest["tests_failed"] == 3
    assert manifest["compile_errors"] == 3
    assert result["screenshot_count"] == 0
    media_types = {e.get("media_type") for e in manifest["entries"]}
    assert "diagnostic_text" in media_types
    assert "screenshot" not in media_types


def test_finalize_partial_on_execution_failure(tmp_path):
    """Deliverable + evidence reflect failed execution without orchestrator changes."""
    from src.reporting.excel_deliverable_report import build_deliverable

    run_id = "run-partial-exec"
    project_dir = tmp_path / "proj"
    artifact_dir = project_dir / "aptp-artifacts" / run_id
    artifact_dir.mkdir(parents=True)

    exec_result = {
        "mode": "live",
        "status": "failed",
        "passed": 0,
        "failed": 3,
        "total": 0,
        "pass_rate": 0.0,
        "duration_seconds": 0.03,
        "executed_tests": [],
        "compile_errors": 3,
        "failures": [
            {
                "title": "OBL-001",
                "error": {"message": "SyntaxError: duplicate import", "category": "compile"},
            }
        ],
        "stderr": "SyntaxError: duplicate import",
        "artifacts": {"screenshots": [], "traces": [], "videos": []},
    }
    collect_and_persist_evidence(
        run_id,
        project_dir,
        exec_result,
        project_id="demo",
        target_url="https://example.com",
    )

    obligations = [
        {"id": "OBL-001", "title": "Crear inscripción", "automation_tier": "automated"},
    ]
    deliverable_result = build_deliverable(
        artifact_dir / "deliverable.xlsx",
        metadata={"inc": "INC999", "objetivo": "Test", "alcance": "Scope"},
        obligations=obligations,
        execution_results=exec_result,
        run_id=run_id,
    )
    assert deliverable_result["validation"]["valid"] is True

    summary = {
        "run_id": run_id,
        "project_id": "demo",
        "status": "partial",
        "results": {
            "generated_code": {"project_path": str(project_dir)},
            "execution": exec_result,
            "reports": {},
        },
    }
    write_run_reports_to_disk(run_id, summary)
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["execution_status"] == "failed"
    assert manifest["compile_errors"] == 3
    assert manifest["tests_failed"] == 3

    pdf_path = artifact_dir / "execution-report.pdf"
    assert pdf_path.is_file()
    import fitz

    doc = fitz.open(str(pdf_path))
    pdf_text = "".join(page.get_text() for page in doc)
    doc.close()
    assert "SyntaxError" in pdf_text or "duplicate import" in pdf_text


def test_build_execution_pdf_embeds_diagnostic_text(tmp_path):
    evidence_root = tmp_path / "evidence"
    diag_dir = evidence_root / "artifacts" / "compile"
    diag_dir.mkdir(parents=True)
    (diag_dir / "stdout.txt").write_text("SyntaxError: duplicate captureStepEvidence import", encoding="utf-8")

    manifest = {
        "entries": [
            {
                "test_id": "compile",
                "step_id": "stdout",
                "label": "Execution log excerpt",
                "path": "artifacts/compile/stdout.txt",
                "media_type": "diagnostic_text",
            }
        ]
    }
    pdf_path = tmp_path / "execution-report.pdf"
    build_execution_pdf(
        pdf_path,
        run_id="run-1",
        project_id="demo",
        target_url="https://example.com",
        exec_result={
            "mode": "live",
            "status": "failed",
            "passed": 0,
            "failed": 1,
            "total": 0,
            "pass_rate": 0.0,
            "duration_seconds": 0.1,
            "executed_tests": [],
            "failures": [],
        },
        manifest=manifest,
        evidence_root=evidence_root,
    )
    import fitz

    doc = fitz.open(str(pdf_path))
    text = "".join(page.get_text() for page in doc)
    doc.close()
    assert "captureStepEvidence" in text
    assert "(could not embed" not in text


def test_artifact_zip_bundles(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    run_id = "run-zip"
    artifact_dir = project / "aptp-artifacts" / run_id
    scripts = artifact_dir / "scripts" / "tests"
    scripts.mkdir(parents=True)
    (scripts / "a.spec.ts").write_text("// spec", encoding="utf-8")
    evidence = artifact_dir / "evidence" / "screenshots"
    evidence.mkdir(parents=True)
    (evidence / "shot.png").write_bytes(b"png")
    (artifact_dir / "execution-report.pdf").write_bytes(b"%PDF-1.4 mock")
    (artifact_dir / "reports.json").write_text("{}", encoding="utf-8")

    summary = {
        "run_id": run_id,
        "project_id": "demo",
        "status": "completed",
        "results": {
            "generated_code": {"project_path": str(project)},
            "reports": {"automation": {}},
        },
    }
    monkeypatch.setattr(get_memory(), "read", lambda key: summary if key == f"pipeline_summary:{run_id}" else None)

    scripts_zip, _ = build_run_artifact_zip(run_id, bundle="scripts")
    with zipfile.ZipFile(BytesIO(scripts_zip)) as zf:
        names = zf.namelist()
        assert any("scripts/tests/a.spec.ts" in n for n in names)

    evidence_zip, _ = build_run_artifact_zip(run_id, bundle="evidence")
    with zipfile.ZipFile(BytesIO(evidence_zip)) as zf:
        names = zf.namelist()
        assert any("execution-report.pdf" in n for n in names)

    write_run_reports_to_disk(run_id, summary)
    manifest = json.loads((artifact_dir / "manifest.json").read_text())
    assert manifest["bundles"]["scripts"]
    assert manifest["inventory"]["script_files"] >= 1
