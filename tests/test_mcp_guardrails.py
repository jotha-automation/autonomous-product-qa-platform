"""MCP guardrails allow asset resolver tools."""
from src.agents.guardrails.agent_guardrails import (
    DELIVERABLE_MCP_TOOLS,
    INTAKE_MCP_TOOLS,
    validate_mcp_tool_scope,
)


def test_asset_resolver_tools_allowed():
    for tool in (
        "identify_sensitive_fields",
        "list_excel_sheets",
        "register_runtime_secret",
        "propose_test_asset_binding",
    ):
        validate_mcp_tool_scope(tool, "proj-1", {})


def test_explore_loop_blocks_secret_registration():
    import pytest

    with pytest.raises(ValueError, match="not allowed"):
        validate_mcp_tool_scope(
            "register_runtime_secret",
            "proj-1",
            {},
            intake_exploration_only=True,
        )


def test_deliverable_tools_allowed_in_full_scope():
    for tool in DELIVERABLE_MCP_TOOLS:
        validate_mcp_tool_scope(tool, "proj-1", {})
