"""Test asset resolver offline fallback when MCP is unavailable."""
from __future__ import annotations

import pytest

from src.agents.test_asset_resolver import TestAssetResolverAgent
from src.services.mcp_client import mcp_intake_available


@pytest.mark.asyncio
async def test_resolve_offline_from_excel_extra(monkeypatch):
    monkeypatch.setattr("src.agents.test_asset_resolver.mcp_intake_available", lambda: False)

    agent = TestAssetResolverAgent()
    interpreted = {
        "intake_mode": "content_first",
        "confidence": 0.8,
        "environment": {"target_url": "https://example.com"},
        "obligations": [
            {
                "id": "OBL-001",
                "title": "Login test",
                "steps": ["Ingresar al portal con credenciales"],
                "required_assets": ["login_user"],
                "automation_tier": "automated",
            }
        ],
        "assets": [],
        "asset_bindings": [],
    }
    documents = [
        {
            "filename": "credenciales.xlsx",
            "extra": {
                "intake_role": "ASSET_BUNDLE",
                "sheets": [
                    {
                        "name": "Usuarios",
                        "headers": ["cliente", "clave", "token"],
                        "rows": [
                            ["cliente", "clave", "token"],
                            ["312", "123456", "63866019"],
                        ],
                    }
                ],
            },
        }
    ]

    result = await agent.execute(
        {
            "run_id": "run-offline-test",
            "project_id": "proj-1",
            "interpreted_intake": interpreted,
            "documents": documents,
        }
    )

    assert result["resolution_mode"] == "offline"
    assert result["asset_count"] >= 1
    assert result["asset_bindings"]
    assert mcp_intake_available() or result["mcp_audit"] == []
