"""MCP client wrapper for aptp-intake-tools (in-process when module available)."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.guardrails.agent_guardrails import MCP_MAX_ITERATIONS, validate_mcp_tool_scope

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("mcp_client")

_MCP_AVAILABLE: Optional[bool] = None


def mcp_intake_available() -> bool:
    """MCP intake tools are enabled when the in-process module exists — no env toggle."""
    global _MCP_AVAILABLE
    if _MCP_AVAILABLE is not None:
        return _MCP_AVAILABLE
    try:
        spec = importlib.util.find_spec("mcp.aptp_intake_tools.tools")
        _MCP_AVAILABLE = spec is not None
    except ModuleNotFoundError:
        _MCP_AVAILABLE = False
    return _MCP_AVAILABLE


class MCPClient:
    """Agent-facing MCP tool client."""

    def __init__(self, ctx):
        if not mcp_intake_available():
            raise RuntimeError("MCP intake tools module not available")
        from mcp.aptp_intake_tools.tools import get_intake_tools

        self.ctx = ctx
        self.tools = get_intake_tools(ctx)
        self.max_iterations = MCP_MAX_ITERATIONS
        self.project_id = ctx.project_id

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = arguments or {}
        validate_mcp_tool_scope(name, self.project_id, args)
        return self.tools.call(name, args)

    async def explore_loop(
        self,
        steps: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Execute a sequence of tool calls (agent-provided or scripted)."""
        results = []
        for step in steps[: self.max_iterations]:
            name = step.get("tool")
            args = step.get("arguments", {})
            if not name:
                continue
            validate_mcp_tool_scope(name, self.project_id, args, intake_exploration_only=True)
            result = self.tools.call(name, args)
            results.append({"tool": name, "result": result})
        return results

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return list(self.ctx.audit_log)

    def redacted_audit_log(self) -> List[Dict[str, Any]]:
        store = self.ctx.secret_store
        return store.redact_dict(self.ctx.audit_log)


def create_mcp_client(
    project_id: str,
    run_id: str,
    documents: List[Dict[str, Any]],
    *,
    artifact_dir: Optional[Path] = None,
) -> MCPClient:
    if not mcp_intake_available():
        raise RuntimeError("MCP intake tools module not available")
    from mcp.aptp_intake_tools.tools import IntakeToolContext

    ctx = IntakeToolContext(
        project_id=project_id,
        run_id=run_id,
        documents=documents,
        artifact_dir=artifact_dir,
    )
    return MCPClient(ctx)
