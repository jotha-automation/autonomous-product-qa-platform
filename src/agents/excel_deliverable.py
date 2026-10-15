"""Excel deliverable agent — populates E59 template from interpreted intake + execution."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.reporting.excel_deliverable_report import build_alcance_text, build_deliverable, build_deliverable_rows, drive_folder_url
from src.reporting.infra_failures import (
    apply_infrastructure_execution_semantics,
    detect_execution_infrastructure_blocker,
)
from src.intake.test_naming import format_test_set_name, infer_test_set_type

logger = logging.getLogger("excel_deliverable")

_INC_RE = re.compile(r"INC\s*(\d+)", re.I)


class ExcelDeliverableAgent(BaseAgent):
    """Generate Excel deliverable from interpreted intake and execution results.

    Memory contract:
      Reads: interpreted_intake, execution_results, test_plan, functional_scenarios
      Writes: deliverable
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        run_id = context.get("run_id", "local")
        project_id = context.get("project_id", "unknown")
        documents: List[dict] = context.get("documents") or []
        interpreted = context.get("interpreted_intake") or self.read_memory("interpreted_intake", {})
        execution_raw = context.get("execution_results") or context.get("execution") or {}
        execution = execution_raw if isinstance(execution_raw, dict) else {}
        # Align Excel verdicts with the visual report: when the run was blocked by
        # an environment/infrastructure failure, reclassify counters/rows so failed
        # tests are surfaced as blocked rather than product rejections.
        rca_raw = context.get("failure_rca") or self.read_memory("failure_rca", {})
        rca = rca_raw if isinstance(rca_raw, dict) else {}
        infra_blocker = detect_execution_infrastructure_blocker(execution, rca)
        if infra_blocker:
            execution = apply_infrastructure_execution_semantics(execution, infra_blocker)
        env = interpreted.get("environment") or {}
        functional_scenarios = (
            context.get("functional_scenarios")
            or (context.get("test_design") or {}).get("functional_scenarios")
            or []
        )
        test_plan = context.get("test_plan") or {}
        plan_scenarios = test_plan.get("scenarios") or []
        if plan_scenarios:
            merged_scenarios = list(functional_scenarios)
            seen_ids = {str(s.get("id")) for s in merged_scenarios if s.get("id")}
            for sc in plan_scenarios:
                sid = str(sc.get("id") or "")
                if sid and sid not in seen_ids:
                    merged_scenarios.append(sc)
                    seen_ids.add(sid)
            functional_scenarios = merged_scenarios

        artifact_dir = context.get("artifact_dir")
        if artifact_dir:
            dest = Path(artifact_dir) / "deliverable.xlsx"
        else:
            dest = Path("generated-test-projects") / project_id / "aptp-artifacts" / run_id / "deliverable.xlsx"
        dest.parent.mkdir(parents=True, exist_ok=True)

        dm = interpreted.get("deliverable_metadata") or {}
        obligations = interpreted.get("obligations") or []
        from src.automation.obligation_dedup import dedupe_obligations

        obligations = dedupe_obligations([o for o in obligations if isinstance(o, dict)])
        run_metadata = context.get("metadata") or {}
        drive_folder_id = (
            context.get("drive_folder_id")
            or run_metadata.get("drive_folder_id")
            or ""
        )
        metadata = self._build_metadata(
            dm,
            documents,
            run_id,
            project_id,
            obligations=obligations,
            drive_folder_id=str(drive_folder_id),
            target_url=str(context.get("target_url") or env.get("target_url") or ""),
        )

        result = await asyncio.to_thread(
            build_deliverable,
            dest,
            metadata=metadata,
            obligations=obligations,
            execution_results=execution,
            functional_scenarios=functional_scenarios,
            run_id=run_id,
        )
        rows = build_deliverable_rows(
            obligations,
            functional_scenarios=functional_scenarios,
            execution_results=execution,
            run_id=run_id,
        )

        self.write_memory("deliverable", result)
        return {
            "deliverable_path": result.get("path"),
            "validation": result.get("validation"),
            "obligation_rows": rows,
            "obligation_row_count": result.get("test_rows_appended", 0),
            "obligation_count": result.get("obligation_count", len(obligations)),
            "template_rows_cleared": result.get("template_rows_cleared", 0),
            "documentation_url": metadata.get("documentation_url"),
        }

    @staticmethod
    def _build_metadata(
        dm: Dict[str, Any],
        documents: List[dict],
        run_id: str,
        project_id: str,
        *,
        obligations: List[Dict[str, Any]],
        drive_folder_id: str = "",
        target_url: str = "",
    ) -> Dict[str, Any]:
        inc = dm.get("inc_id") or dm.get("inc")
        if not inc:
            for doc in documents:
                name = doc.get("filename") or ""
                match = _INC_RE.search(name)
                if match:
                    inc = f"INC{match.group(1)}"
                    break
        objetivo = dm.get("objective") or dm.get("objetivo")
        if not objetivo and documents:
            for doc in documents:
                if (doc.get("filename") or "").lower().endswith(".pdf"):
                    objetivo = (doc.get("filename") or "")[:200]
                    break
        scope_summary = dm.get("scope") or dm.get("alcance") or ""
        alcance = build_alcance_text(
            obligations,
            project_id=project_id,
            run_id=run_id,
            summary=scope_summary,
        )
        doc_url = drive_folder_url(drive_folder_id)
        objetivo_text = objetivo or ""
        test_set_type = infer_test_set_type(dm, obligations=obligations)
        suite_title = dm.get("suite_title") or format_test_set_name(test_set_type, project_id)
        if not dm.get("suite_title") and objetivo_text and objetivo_text != project_id:
            # C10 section subtitle from objective when present
            dm_suite_section = dm.get("suite_section") or objetivo_text[:200]
        else:
            dm_suite_section = dm.get("suite_section") or "CASOS DE PRUEBA"
        app_platform = dm.get("app_platform") or dm.get("platform") or ""
        if not app_platform and target_url:
            app_platform = f"Web — {target_url}"[:120]
        return {
            "inc": inc or "",
            "tec": dm.get("tec_id") or dm.get("tec") or "",
            "objetivo": objetivo_text,
            "alcance": alcance if obligations or scope_summary else "",
            "suite_title": suite_title,
            "suite_section": dm_suite_section,
            "test_set_type": test_set_type,
            "documentation_url": doc_url,
            "documentation_label": (
                f"Documentación técnica APTP — {project_id} (Google Drive)"
                if doc_url
                else ""
            ),
            "app_platform": app_platform,
            "start_date": dm.get("start_date") or "",
            "cert_date": dm.get("cert_date") or "",
        }
