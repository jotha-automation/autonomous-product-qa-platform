"""MCP server entrypoint for aptp-intake-tools."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_spec = importlib.util.spec_from_file_location("aptp_intake_tools_impl", _PKG_DIR / "tools.py")
_tools = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_tools)

IntakeToolContext = _tools.IntakeToolContext

TOOL_REGISTRY = {
    "list_intake_files": lambda ctx, args: _tools.list_intake_files(ctx),
    "describe_document": lambda ctx, args: _tools.describe_document(ctx, args["file_id_or_path"]),
    "list_excel_sheets": lambda ctx, args: _tools.list_excel_sheets(ctx, args["file_id_or_path"]),
    "read_excel_range": lambda ctx, args: _tools.read_excel_range(
        ctx, args["file_id_or_path"], args["sheet_name"], args.get("cell_range", "A1:Z100")
    ),
    "read_pdf_pages": lambda ctx, args: _tools.read_pdf_pages(ctx, args["file_id_or_path"], args["page_numbers"]),
    "search_intake_text": lambda ctx, args: _tools.search_intake_text(ctx, args["query"], args.get("limit", 20)),
    "find_file_by_hint": lambda ctx, args: _tools.find_file_by_hint(ctx, args["hint"]),
    "identify_sensitive_fields": lambda ctx, args: _tools.identify_sensitive_fields(
        ctx, args["file_id_or_path"], args.get("sheet_name", "")
    ),
    "register_runtime_secret": lambda ctx, args: _tools.register_runtime_secret(
        ctx, args["key"], args["value"], args.get("source", "")
    ),
    "get_runtime_secret": lambda ctx, args: _tools.get_runtime_secret(ctx, args["key"]),
    "propose_test_asset_binding": lambda ctx, args: _tools.propose_test_asset_binding(
        ctx, args["obligation_id"], args.get("asset_keys", [])
    ),
    "list_qa_obligations": lambda ctx, args: _tools.get_intake_tools(ctx).call("list_qa_obligations", args or {}),
    "build_scenario_outline": lambda ctx, args: _tools.get_intake_tools(ctx).call("build_scenario_outline", args or {}),
    "get_deliverable_template_info": lambda ctx, args: _tools.get_deliverable_template_info(ctx),
    "clone_deliverable_template": lambda ctx, args: _tools.clone_deliverable_template(
        ctx, args.get("dest_name", "deliverable.xlsx")
    ),
    "write_deliverable_cells": lambda ctx, args: _tools.write_deliverable_cells(ctx, args["cells"]),
    "append_deliverable_test_rows": lambda ctx, args: _tools.append_deliverable_test_rows(ctx, args["rows"]),
    "validate_deliverable_structure": lambda ctx, args: _tools.validate_deliverable_structure(ctx),
}


def handle_tool_call(ctx: IntakeToolContext, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    handler = TOOL_REGISTRY.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}
    return handler(ctx, arguments)


def main() -> None:
    ctx: IntakeToolContext | None = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if req.get("type") == "init":
            ctx = IntakeToolContext(
                run_id=req.get("run_id", "stdio"),
                project_id=req.get("project_id", "unknown"),
                documents=req.get("documents", []),
            )
            print(json.dumps({"type": "init_ok"}), flush=True)
            continue
        if ctx is None:
            print(json.dumps({"error": "not initialized"}), flush=True)
            continue
        result = handle_tool_call(ctx, req.get("tool", ""), req.get("arguments", {}))
        print(json.dumps({"tool": req.get("tool"), "result": result}), flush=True)


if __name__ == "__main__":
    main()
