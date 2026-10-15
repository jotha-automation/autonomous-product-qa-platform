"""Artifact bundle download for partial and full runs."""
import zipfile
from io import BytesIO

import pytest

from src.memory.enhanced_memory import get_memory
from src.services.artifact_bundle import build_run_artifact_zip, write_run_reports_to_disk


def test_partial_run_full_bundle_download(tmp_path, monkeypatch):
    run_id = "run-partial-test01"
    project_id = "TEST-PARTIAL"
    project_dir = tmp_path / "generated-test-projects" / project_id
    project_dir.mkdir(parents=True)

    summary = {
        "run_id": run_id,
        "project_id": project_id,
        "status": "partial",
        "results": {
            "readiness": {"status": "READY"},
            "reports": {},
        },
    }

    monkeypatch.setattr(
        "src.services.artifact_bundle.GENERATED_PROJECTS_ROOT",
        tmp_path / "generated-test-projects",
    )
    monkeypatch.setattr(get_memory(), "read", lambda key: summary if key == f"pipeline_summary:{run_id}" else None)

    write_run_reports_to_disk(run_id, summary)
    artifact_dir = project_dir / "aptp-artifacts" / run_id
    assert (artifact_dir / "pipeline_summary.json").is_file()
    assert (artifact_dir / "manifest.json").is_file()

    zip_bytes, filename = build_run_artifact_zip(run_id, bundle="full")
    assert filename.endswith("-full.zip")

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert any("pipeline_summary.json" in n for n in names)
        assert any("manifest.json" in n for n in names)


def test_full_bundle_includes_scripts_and_evidence_at_root(tmp_path, monkeypatch):
    run_id = "run-full-bundle"
    project_id = "TEST-FULL"
    project_dir = tmp_path / "generated-test-projects" / project_id
    artifact_dir = project_dir / "aptp-artifacts" / run_id
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
        "project_id": project_id,
        "status": "completed",
        "results": {
            "generated_code": {"project_path": str(project_dir)},
            "reports": {},
        },
    }

    monkeypatch.setattr(
        "src.services.artifact_bundle.GENERATED_PROJECTS_ROOT",
        tmp_path / "generated-test-projects",
    )
    monkeypatch.setattr(get_memory(), "read", lambda key: summary if key == f"pipeline_summary:{run_id}" else None)

    zip_bytes, _ = build_run_artifact_zip(run_id, bundle="full")
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert any(n.startswith("scripts/") and n.endswith("a.spec.ts") for n in names)
        assert any(n.startswith("evidence/") for n in names)
        assert "execution-report.pdf" in names
        assert any("aptp-artifacts/" in n and "reports.json" in n for n in names)


def test_scripts_bundle_missing_for_partial_run(tmp_path, monkeypatch):
    run_id = "run-partial-test02"
    project_id = "TEST-PARTIAL-2"
    (tmp_path / "generated-test-projects" / project_id).mkdir(parents=True)

    summary = {
        "run_id": run_id,
        "project_id": project_id,
        "status": "partial",
        "results": {"reports": {}},
    }

    monkeypatch.setattr(
        "src.services.artifact_bundle.GENERATED_PROJECTS_ROOT",
        tmp_path / "generated-test-projects",
    )
    monkeypatch.setattr(get_memory(), "read", lambda key: summary if key == f"pipeline_summary:{run_id}" else None)

    write_run_reports_to_disk(run_id, summary)

    with pytest.raises(FileNotFoundError, match="No Playwright scripts"):
        build_run_artifact_zip(run_id, bundle="scripts")
