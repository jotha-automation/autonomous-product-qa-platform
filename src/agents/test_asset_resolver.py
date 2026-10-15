"""Resolve test assets and credentials via MCP exploration."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.intake.schemas.interpreted import AssetBinding, InterpretedIntake, SourceRef, TestAsset
from src.services.mcp_client import create_mcp_client, mcp_intake_available
from src.services.run_secret_store import SENSITIVE_KEY_PATTERN, RunSecretStore

logger = logging.getLogger("test_asset_resolver")


class TestAssetResolverAgent(BaseAgent):
    """Resolve test assets and credentials from intake documents.

    Memory contract:
      Reads: interpreted_intake, context["documents"]
      Writes: interpreted_intake (asset_bindings), assets, asset_bindings
    """

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        interpreted_raw = context.get("interpreted_intake") or self.read_memory("interpreted_intake")
        if not interpreted_raw:
            return {"assets": [], "asset_bindings": [], "skipped": True}

        interpreted = InterpretedIntake.from_dict(interpreted_raw)
        documents = context.get("documents") or []
        run_id = context.get("run_id", "local")
        project_id = context.get("project_id", "unknown")

        asset_bundles = [
            d
            for d in documents
            if (d.get("extra") or {}).get("intake_role") == "ASSET_BUNDLE"
            or d.get("intake_role") == "ASSET_BUNDLE"
            or str(d.get("filename", "")).lower().endswith((".xlsx", ".csv", ".xls"))
        ]

        if mcp_intake_available():
            return await self._resolve_via_mcp(
                interpreted, asset_bundles, documents, project_id, run_id
            )

        logger.warning(
            "MCP intake tools unavailable — resolving assets from parsed document metadata (run=%s)",
            run_id,
        )
        return self._resolve_offline(interpreted, asset_bundles, run_id, documents)

    async def _resolve_via_mcp(
        self,
        interpreted: InterpretedIntake,
        asset_bundles: List[dict],
        documents: List[dict],
        project_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        mcp = create_mcp_client(project_id, run_id, documents)
        store = RunSecretStore.for_run(run_id)
        assets: List[TestAsset] = []

        for doc in asset_bundles:
            from src.intake.asset_bundle_parser import parse_asset_bundle_document

            parsed = parse_asset_bundle_document(doc)
            if parsed:
                for asset, raw_fields in parsed:
                    assets.append(asset)
                    self._register_secrets(store, asset, raw_fields, doc.get("filename", ""))
                continue
            file_id = doc.get("id") or doc.get("drive_file_id") or doc.get("filename", "")
            sens = await mcp.call_tool("identify_sensitive_fields", {"file_id": file_id})
            sheets_result = await mcp.call_tool("list_excel_sheets", {"file_id": file_id})
            for asset, raw_fields in self._iter_assets_from_doc(doc, sens, sheets_result):
                assets.append(asset)
                self._register_secrets(store, asset, raw_fields, doc.get("filename", ""))

        bindings = self._build_bindings(interpreted.obligations, assets)
        from src.automation.bank_portal import is_bank_portal_context, register_demo_secrets

        if is_bank_portal_context({"interpreted_intake": interpreted.to_dict()}, obligations=[o.model_dump() for o in interpreted.obligations]):
            needs_login = any(
                "login_user" in (o.required_assets or [])
                or any(tok in " ".join(o.steps).lower() for tok in ("login", "ingres", "token"))
                for o in interpreted.obligations
            )
            if needs_login:
                register_demo_secrets(run_id)
            if needs_login and not any(a.key == "login_user" for a in assets):
                assets.append(
                    TestAsset(
                        key="login_user",
                        asset_type="credential",
                        source_ref=SourceRef(filename="bank_portal_demo", excerpt="INC1353166 demo creds"),
                        fields={
                            "cliente": "[REDACTED]",
                            "documento": "[REDACTED]",
                            "clave": "[REDACTED]",
                            "token": "[REDACTED]",
                        },
                        usage_hint="bank portal login",
                        sensitive_field_keys=["cliente", "documento", "clave", "token"],
                    )
                )
                bindings = self._build_bindings(interpreted.obligations, assets)
        for binding in bindings:
            await mcp.call_tool(
                "propose_test_asset_binding",
                {"obligation_id": binding.obligation_id, "asset_keys": binding.asset_keys},
            )

        return self._finalize_resolution(interpreted, assets, bindings, documents, mode="mcp", mcp_audit=mcp.redacted_audit_log())

    def _resolve_offline(
        self,
        interpreted: InterpretedIntake,
        asset_bundles: List[dict],
        run_id: str,
        documents: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        store = RunSecretStore.for_run(run_id)
        assets: List[TestAsset] = []

        for doc in asset_bundles:
            from src.intake.asset_bundle_parser import parse_asset_bundle_document

            parsed = parse_asset_bundle_document(doc)
            if parsed:
                for asset, raw_fields in parsed:
                    assets.append(asset)
                    self._register_secrets(store, asset, raw_fields, doc.get("filename", ""))
                continue
            for asset, raw_fields in self._iter_assets_from_doc(doc, {"fields": []}, {}):
                assets.append(asset)
                self._register_secrets(store, asset, raw_fields, doc.get("filename", ""))

        bindings = self._build_bindings(interpreted.obligations, assets)
        from src.automation.bank_portal import is_bank_portal_context, register_demo_secrets

        if is_bank_portal_context({"interpreted_intake": interpreted.to_dict()}, obligations=[o.model_dump() for o in interpreted.obligations]):
            needs_login = any(
                "login_user" in (o.required_assets or [])
                or any(tok in " ".join(o.steps).lower() for tok in ("login", "ingres", "token"))
                for o in interpreted.obligations
            )
            if needs_login:
                register_demo_secrets(run_id)
            if needs_login and not any(a.key == "login_user" for a in assets):
                assets.append(
                    TestAsset(
                        key="login_user",
                        asset_type="credential",
                        source_ref=SourceRef(filename="bank_portal_demo", excerpt="INC1353166 demo creds"),
                        fields={
                            "cliente": "[REDACTED]",
                            "documento": "[REDACTED]",
                            "clave": "[REDACTED]",
                            "token": "[REDACTED]",
                        },
                        usage_hint="bank portal login",
                        sensitive_field_keys=["cliente", "documento", "clave", "token"],
                    )
                )
                bindings = self._build_bindings(interpreted.obligations, assets)
        return self._finalize_resolution(interpreted, assets, bindings, documents or [], mode="offline")

    def _finalize_resolution(
        self,
        interpreted: InterpretedIntake,
        assets: List[TestAsset],
        bindings: List[AssetBinding],
        documents: List[dict],
        *,
        mode: str,
        mcp_audit: Optional[List] = None,
    ) -> Dict[str, Any]:
        interpreted.assets = assets
        interpreted.asset_bindings = bindings
        self.write_memory("interpreted_intake", interpreted.to_dict())
        self.write_memory("asset_bindings", [b.model_dump() for b in bindings])
        catalog = next((d.get("intake_catalog") for d in documents if d.get("intake_catalog")), None)
        if catalog:
            self.write_memory("intake_catalog", catalog)

        result = {
            "assets": [a.model_dump() for a in assets],
            "asset_bindings": [b.model_dump() for b in bindings],
            "asset_count": len(assets),
            "interpreted_intake": interpreted.public_dict(),
            "mcp_audit": mcp_audit or [],
            "resolution_mode": mode,
        }
        if catalog:
            result["intake_catalog"] = catalog
        return result

    def _register_secrets(
        self,
        store: RunSecretStore,
        asset: TestAsset,
        raw_fields: Dict[str, str],
        source: str,
    ) -> None:
        if asset.asset_type == "credential":
            for field_key, value in raw_fields.items():
                if value and value != "[REDACTED]":
                    store.register(f"{asset.key}.{field_key}", value, source=source)
            return
        for field_key in asset.sensitive_field_keys:
            value = raw_fields.get(field_key, "")
            if value and value != "[REDACTED]":
                store.register(f"{asset.key}_{field_key}", value, source=source)

    def _iter_assets_from_doc(
        self, doc: dict, sens: dict, sheets_result: dict
    ):
        extra = doc.get("extra") or {}
        sheets = extra.get("sheets") or []
        sensitive_cols = {f["header"] for f in sens.get("fields", []) if f.get("header")}

        for sheet in sheets:
            rows = sheet.get("rows") or []
            headers = sheet.get("headers") or (rows[0] if rows else [])
            for ri, row in enumerate(rows[1:51], start=2):
                raw_fields: Dict[str, str] = {}
                for ci, header in enumerate(headers):
                    if ci < len(row) and header:
                        raw_fields[str(header)] = str(row[ci])
                if not any(raw_fields.values()):
                    continue
                sensitive_keys = [
                    h for h in raw_fields if h in sensitive_cols or SENSITIVE_KEY_PATTERN.search(h)
                ]
                key = f"dataset_{sheet.get('name', 'sheet')}_row_{ri}".replace(" ", "_").lower()
                yield (
                    TestAsset(
                        key=key,
                        asset_type="credential" if sensitive_keys else "tabular_row",
                        source_ref=SourceRef(
                            filename=doc.get("filename", ""),
                            location=f"{sheet.get('name')}!{ri}",
                        ),
                        fields={
                            k: ("[REDACTED]" if k in sensitive_keys else v)
                            for k, v in raw_fields.items()
                        },
                        usage_hint="login or test data row",
                        sensitive_field_keys=sensitive_keys,
                    ),
                    raw_fields,
                )

    def _extract_assets_from_doc(
        self, doc: dict, sens: dict, sheets_result: dict
    ) -> List[TestAsset]:
        return [asset for asset, _ in self._iter_assets_from_doc(doc, sens, sheets_result)][:50]

    def _build_bindings(self, obligations, assets) -> List[AssetBinding]:
        bindings = []
        cred_assets = [a for a in assets if a.asset_type == "credential"]
        cred_keys = [a.key for a in cred_assets]
        for obl in obligations:
            keys = []
            if obl.required_assets:
                for req in obl.required_assets:
                    match = next((a.key for a in assets if req in a.key or req in a.usage_hint), None)
                    if match:
                        keys.append(match)
            if not keys and cred_keys and any(
                tok in " ".join(obl.steps).lower() for tok in ("login", "ingres", "autentic")
            ):
                keys = cred_keys[:1]
            if keys:
                bindings.append(AssetBinding(obligation_id=obl.id, asset_keys=keys))
        return bindings
