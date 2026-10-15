"""Cross-agent guardrails: scope, credentials, MCP limits, codegen safety."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

MCP_MAX_ITERATIONS = 12

INTAKE_MCP_TOOLS = frozenset(
    {
        "list_intake_files",
        "describe_document",
        "list_excel_sheets",
        "read_excel_range",
        "read_pdf_pages",
        "search_intake_text",
        "find_file_by_hint",
        "identify_sensitive_fields",
        "register_runtime_secret",
        "propose_test_asset_binding",
        "list_qa_obligations",
        "build_scenario_outline",
        # legacy aliases
        "read_intake_file",
        "read_intake_excel_sheet",
        "read_intake_pdf_pages",
    }
)

EXECUTION_MCP_TOOLS = frozenset({"get_runtime_secret"})

DELIVERABLE_MCP_TOOLS = frozenset(
    {
        "get_deliverable_template_info",
        "clone_deliverable_template",
        "write_deliverable_cells",
        "append_deliverable_test_rows",
        "validate_deliverable_structure",
    }
)

ALLOWED_MCP_TOOLS = INTAKE_MCP_TOOLS | EXECUTION_MCP_TOOLS

CREDENTIAL_PATTERNS = re.compile(
    r"(password\s*[:=]\s*['\"]?[^\s'\"]{4,}|api[_-]?key\s*[:=]\s*['\"]?[^\s'\"]{8,}|"
    r"secret\s*[:=]\s*['\"]?[^\s'\"]{4,}|Bearer\s+[A-Za-z0-9\-_.]+)",
    re.I,
)

UNSAFE_SHELL_PATTERNS = re.compile(
    r"\b(execSync|spawnSync|child_process|os\.system|subprocess\.|shell:\s*true|"
    r"eval\s*\(|Function\s*\(|require\s*\(\s*['\"]child_process|"
    r"`[^`]*(?:rm\s+-rf|curl\s+[^\s]+\s*\|\s*(?:ba)?sh)\`)\b",
    re.I,
)


def validate_mcp_tool_scope(
    tool_name: str,
    project_id: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    intake_exploration_only: bool = False,
) -> None:
    """Restrict MCP tool calls to known intake or deliverable tools."""
    if intake_exploration_only:
        allowed = INTAKE_MCP_TOOLS - {"register_runtime_secret", "propose_test_asset_binding"}
    else:
        allowed = INTAKE_MCP_TOOLS | DELIVERABLE_MCP_TOOLS | EXECUTION_MCP_TOOLS
    if tool_name not in allowed:
        raise ValueError(f"MCP tool '{tool_name}' is not allowed in intake scope for project {project_id}")
    args = arguments or {}
    scoped_project = args.get("project_id")
    if scoped_project and scoped_project != project_id:
        raise ValueError(f"MCP tool scope mismatch: expected project {project_id}")


def redact_credentials_from_text(text: str) -> str:
    """Strip credential-like values from report-bound text."""
    if not text:
        return text
    return CREDENTIAL_PATTERNS.sub("[REDACTED]", text)


def redact_dict_values(data: Any) -> Any:
    """Recursively redact credential patterns in dict/list structures."""
    if isinstance(data, dict):
        return {k: redact_dict_values(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_dict_values(item) for item in data]
    if isinstance(data, str):
        return redact_credentials_from_text(data)
    return data


def validate_target_url(url: Optional[str]) -> None:
    """Reject target URLs that are not on the demo allowlist or look unsafe."""
    from src.services.target_url import is_demo_target_url_allowed

    if not url:
        return
    if not is_demo_target_url_allowed(url):
        raise ValueError(
            f"Target URL '{url}' is not in the demo allowlist. "
            "Add it to the allowlist or contact the platform administrator."
        )


def reject_unsafe_generated_code(code: str, *, filename: str = "") -> List[str]:
    """
    Return list of violations if generated Playwright code contains unsafe shell execution.
    Playwright test APIs (page, expect, test) are allowed; raw shell is not.
    """
    issues: List[str] = []
    if UNSAFE_SHELL_PATTERNS.search(code):
        issues.append(f"Unsafe shell/process execution pattern detected in {filename or 'generated code'}")
    if re.search(r"require\s*\(\s*['\"]fs['\"]\s*\).*writeFile", code, re.I | re.S):
        issues.append(f"Filesystem write outside sandbox in {filename or 'generated code'}")
    return issues
