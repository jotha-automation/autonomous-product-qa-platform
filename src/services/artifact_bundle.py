"""Build downloadable artifact bundles (Playwright project + QA reports)."""
import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.memory.enhanced_memory import get_memory

SCRIPT_SUBDIRS = ("tests", "pages", "fixtures")
SCRIPT_FILES = ("playwright.config.ts", "package.json", ".env", "README.md")

_REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_PROJECTS_ROOT = _REPO_ROOT / "generated-test-projects"


def _project_path_from_summary(summary: Dict[str, Any]) -> Optional[Path]:
    results = summary.get("results") or {}
    gen = results.get("generated_code") or {}
    path = gen.get("project_path")
    if path:
        p = Path(path)
        if p.is_dir():
            return p

    stored = summary.get("artifact_dir")
    if stored:
        artifact = Path(stored)
        if artifact.is_dir() and artifact.parent.name == "aptp-artifacts":
            project = artifact.parent.parent
            if project.is_dir():
                return project

    project_id = summary.get("project_id")
    if project_id:
        return GENERATED_PROJECTS_ROOT / project_id
    return None


def find_artifact_dir_on_disk(run_id: str) -> Optional[Path]:
    """Locate aptp-artifacts/{run_id} under generated-test-projects."""
    if not GENERATED_PROJECTS_ROOT.is_dir():
        return None
    matches = sorted(GENERATED_PROJECTS_ROOT.glob(f"*/aptp-artifacts/{run_id}"))
    for path in matches:
        if path.is_dir():
            return path
    return None


def resolve_artifact_dir(run_id: str, summary: Dict[str, Any]) -> Path:
    """Resolve artifact directory for a run, creating it when needed."""
    project_dir = _project_path_from_summary(summary)
    if project_dir is not None:
        project_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir_for_run(project_dir, run_id)

    existing = find_artifact_dir_on_disk(run_id)
    if existing is not None:
        return existing

    raise FileNotFoundError(f"No artifact directory found for run {run_id}")


def artifact_dir_for_run(project_dir: Path, run_id: str) -> Path:
    artifact_dir = project_dir / "aptp-artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def snapshot_generated_scripts(
    run_id: str,
    project_dir: Path,
    generated_code: Optional[Dict[str, Any]] = None,
) -> Path:
    """Copy generated Playwright sources into aptp-artifacts/{run_id}/scripts/."""
    project_dir = Path(project_dir)
    scripts_dest = artifact_dir_for_run(project_dir, run_id) / "scripts"
    has_sources = any(
        (project_dir / sub).is_dir() and any((project_dir / sub).rglob("*"))
        for sub in SCRIPT_SUBDIRS
    ) or any((project_dir / name).is_file() for name in SCRIPT_FILES)
    if not has_sources and scripts_dest.is_dir() and any(scripts_dest.rglob("*")):
        return scripts_dest
    if scripts_dest.exists():
        shutil.rmtree(scripts_dest)
    scripts_dest.mkdir(parents=True, exist_ok=True)

    for sub in SCRIPT_SUBDIRS:
        src = project_dir / sub
        if src.is_dir():
            shutil.copytree(src, scripts_dest / sub)

    for name in SCRIPT_FILES:
        src = project_dir / name
        if src.is_file():
            shutil.copy2(src, scripts_dest / name)
            if name == ".env":
                from src.services.run_secret_store import RunSecretStore

                store = RunSecretStore.for_run(run_id)
                env_text = (scripts_dest / name).read_text(encoding="utf-8")
                redacted_env = store.redact_text(env_text)
                if redacted_env != env_text:
                    (scripts_dest / name).write_text(redacted_env, encoding="utf-8")

    if generated_code and generated_code.get("files"):
        inventory = {"files": generated_code.get("files", []), "project_path": str(project_dir)}
        (scripts_dest / "generated-inventory.json").write_text(
            json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return scripts_dest


def _inventory_from_artifact_dir(artifact_dir: Path) -> Dict[str, Any]:
    inventory: Dict[str, Any] = {
        "script_files": 0,
        "screenshots": 0,
        "pdf": None,
        "html_report": None,
    }
    scripts_dir = artifact_dir / "scripts"
    if scripts_dir.is_dir():
        inventory["script_files"] = sum(1 for p in scripts_dir.rglob("*") if p.is_file())

    evidence_manifest = artifact_dir / "evidence" / "evidence-manifest.json"
    if evidence_manifest.is_file():
        try:
            data = json.loads(evidence_manifest.read_text(encoding="utf-8"))
            inventory["screenshots"] = data.get("screenshot_count", len(data.get("entries", [])))
        except json.JSONDecodeError:
            pass

    pdf = artifact_dir / "execution-report.pdf"
    if pdf.is_file():
        inventory["pdf"] = "execution-report.pdf"

    evidence_md = artifact_dir / "evidence-report.md"
    if evidence_md.is_file():
        inventory["evidence_report_md"] = "evidence-report.md"

    html = artifact_dir / "evidence" / "html" / "index.html"
    if html.is_file():
        inventory["html_report"] = "evidence/html/index.html"

    return inventory


def _redact_for_run(data: Any, run_id: str) -> Any:
    from src.services.run_secret_store import RunSecretStore

    return RunSecretStore.for_run(run_id).redact_dict(data)


def write_run_reports_to_disk(run_id: str, summary: Dict[str, Any]) -> Optional[Path]:
    """Persist JSON reports under {project}/aptp-artifacts/{run_id}/."""
    try:
        artifact_dir = resolve_artifact_dir(run_id, summary)
    except FileNotFoundError:
        return None

    project_dir = artifact_dir.parent.parent

    reports = (summary.get("results") or {}).get("reports") or {}
    redacted_reports = _redact_for_run(reports, run_id)
    redacted_summary = _redact_for_run(summary, run_id)
    (artifact_dir / "reports.json").write_text(
        json.dumps(redacted_reports, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (artifact_dir / "pipeline_summary.json").write_text(
        json.dumps(redacted_summary, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )

    inventory = _inventory_from_artifact_dir(artifact_dir)
    deliverable = artifact_dir / "deliverable.xlsx"
    if deliverable.is_file():
        inventory["deliverable_xlsx"] = "deliverable.xlsx"

    evidence_data: Dict[str, Any] = {}
    evidence_manifest = artifact_dir / "evidence" / "evidence-manifest.json"
    if evidence_manifest.is_file():
        try:
            evidence_data = json.loads(evidence_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    execution = (summary.get("results") or {}).get("execution") or {}

    manifest = {
        "run_id": run_id,
        "project_id": summary.get("project_id"),
        "status": summary.get("status"),
        "mode": summary.get("results", {}).get("mode", "full"),
        "execution_status": evidence_data.get("execution_status") or execution.get("status"),
        "tests_passed": evidence_data.get("tests_passed", execution.get("passed", 0)),
        "tests_failed": evidence_data.get("tests_failed", execution.get("failed", 0)),
        "compile_errors": evidence_data.get("compile_errors", execution.get("compile_errors", 0)),
        "project_path": str(project_dir),
        "artifact_dir": str(artifact_dir),
        "download_path": f"/api/v1/runs/{run_id}/artifacts",
        "bundles": {
            "full": f"/api/v1/runs/{run_id}/artifacts?bundle=full",
            "scripts": f"/api/v1/runs/{run_id}/artifacts?bundle=scripts",
            "evidence": f"/api/v1/runs/{run_id}/artifacts?bundle=evidence",
            "deliverable": f"/api/v1/runs/{run_id}/artifacts/deliverable.xlsx",
        },
        "manifest_path": f"/api/v1/runs/{run_id}/artifacts/manifest",
        "report_keys": list(reports.keys()),
        "inventory": inventory,
    }
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact_dir


def prepare_artifact_dir_for_download(run_id: str, summary: Dict[str, Any]) -> Path:
    """Ensure on-disk artifacts are complete before building a zip."""
    artifact_dir = find_artifact_dir_on_disk(run_id)
    if not artifact_dir:
        artifact_dir = write_run_reports_to_disk(run_id, summary)
    if not artifact_dir or not artifact_dir.is_dir():
        raise FileNotFoundError(f"No artifacts found for run {run_id}")

    project_dir = _project_path_from_summary(summary)
    if project_dir and project_dir.is_dir():
        generated_code = (summary.get("results") or {}).get("generated_code")
        snapshot_generated_scripts(run_id, project_dir, generated_code)

    refreshed = write_run_reports_to_disk(run_id, summary)
    return refreshed or artifact_dir


def load_run_summary(run_id: str) -> Dict[str, Any]:
    summary = get_memory().read(f"pipeline_summary:{run_id}")
    if summary:
        return summary

    artifact_dir = find_artifact_dir_on_disk(run_id)
    if artifact_dir:
        summary_path = artifact_dir / "pipeline_summary.json"
        if summary_path.is_file():
            return json.loads(summary_path.read_text(encoding="utf-8"))
        manifest_path = artifact_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return {
                "run_id": run_id,
                "project_id": manifest.get("project_id"),
                "status": manifest.get("status", "completed"),
                "artifact_dir": str(artifact_dir),
                "results": {},
            }

    raise FileNotFoundError(f"Run {run_id} not found")


def get_run_manifest(run_id: str) -> Dict[str, Any]:
    artifact_dir = find_artifact_dir_on_disk(run_id)
    if artifact_dir:
        manifest_path = artifact_dir / "manifest.json"
        if manifest_path.is_file():
            return json.loads(manifest_path.read_text(encoding="utf-8"))

    summary = load_run_summary(run_id)
    artifact_dir = write_run_reports_to_disk(run_id, summary)
    if not artifact_dir:
        raise FileNotFoundError(f"No artifact directory available for run {run_id}")
    manifest_path = artifact_dir / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def _zip_directory(zf: zipfile.ZipFile, source: Path, arc_prefix: str) -> None:
    if not source.is_dir():
        return
    for file_path in source.rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(source)
        zf.write(file_path, str(Path(arc_prefix) / rel))


def build_run_artifact_zip(run_id: str, bundle: str = "full") -> Tuple[bytes, str]:
    """Return (zip_bytes, filename) for a completed or partial run."""
    try:
        summary = load_run_summary(run_id)
    except FileNotFoundError:
        summary = {"run_id": run_id, "project_id": "project", "status": "completed", "results": {}}

    artifact_dir = prepare_artifact_dir_for_download(run_id, summary)

    project_id = summary.get("project_id", "project")
    bundle = (bundle or "full").lower()
    filename = f"aptp-{project_id}-{run_id}-{bundle}.zip"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if bundle == "scripts":
            scripts_dir = artifact_dir / "scripts"
            if not scripts_dir.is_dir() or not any(scripts_dir.rglob("*")):
                raise FileNotFoundError("No Playwright scripts were generated for this run")
            _zip_directory(zf, scripts_dir, "scripts")
        elif bundle == "evidence":
            evidence_dir = artifact_dir / "evidence"
            pdf = artifact_dir / "execution-report.pdf"
            evidence_md = artifact_dir / "evidence-report.md"
            if not evidence_dir.is_dir() and not pdf.is_file() and not evidence_md.is_file():
                raise FileNotFoundError("No execution evidence collected for this run")
            if evidence_dir.is_dir():
                _zip_directory(zf, evidence_dir, "evidence")
            if pdf.is_file():
                zf.write(pdf, "execution-report.pdf")
            if evidence_md.is_file():
                zf.write(evidence_md, "evidence-report.md")
        else:
            _zip_directory(zf, artifact_dir, f"aptp-artifacts/{run_id}")
            scripts_dir = artifact_dir / "scripts"
            if scripts_dir.is_dir():
                _zip_directory(zf, scripts_dir, "scripts")
            evidence_dir = artifact_dir / "evidence"
            if evidence_dir.is_dir():
                _zip_directory(zf, evidence_dir, "evidence")
            pdf = artifact_dir / "execution-report.pdf"
            if pdf.is_file():
                zf.write(pdf, "execution-report.pdf")
            evidence_md = artifact_dir / "evidence-report.md"
            if evidence_md.is_file():
                zf.write(evidence_md, "evidence-report.md")
            evidence_md = artifact_dir / "evidence-report.md"
            if evidence_md.is_file():
                zf.write(evidence_md, "evidence-report.md")

    buffer.seek(0)
    return buffer.getvalue(), filename
