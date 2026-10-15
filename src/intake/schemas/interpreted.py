"""Semantic intake model for content-first enterprise QA."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    file_id: str = ""
    filename: str = ""
    relative_path: str = ""
    location: str = ""
    sheet: str = ""
    excerpt: str = ""


class EnvironmentHints(BaseModel):
    target_url: str = ""
    environment: str = ""
    modules: List[str] = Field(default_factory=list)


class TestObligation(BaseModel):
    id: str
    source_refs: List[SourceRef] = Field(default_factory=list)
    title: str
    preconditions: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    expected_results: List[str] = Field(default_factory=list)
    automation_tier: Literal["automated", "manual", "partial"] = "partial"
    automation_blockers: List[str] = Field(default_factory=list)
    automation_rationale: str = ""
    required_assets: List[str] = Field(default_factory=list)


class TestAsset(BaseModel):
    key: str
    asset_type: Literal["credential", "tabular_row", "file", "catalog", "url", "other"] = "other"
    source_ref: SourceRef = Field(default_factory=SourceRef)
    fields: Dict[str, str] = Field(default_factory=dict)
    usage_hint: str = ""
    sensitive: bool = False
    sensitive_field_keys: List[str] = Field(default_factory=list)


class AssetBinding(BaseModel):
    obligation_id: str
    asset_keys: List[str] = Field(default_factory=list)


class DeliverableMeta(BaseModel):
    inc_id: str = ""
    tec_id: str = ""
    objective: str = ""
    scope: str = ""
    release: str = ""
    responsible: str = ""


class InterpretedIntake(BaseModel):
    intake_mode: Literal["content_first", "portfolio_legacy"] = "content_first"
    confidence: float = 0.0
    environment: EnvironmentHints = Field(default_factory=EnvironmentHints)
    obligations: List[TestObligation] = Field(default_factory=list)
    assets: List[TestAsset] = Field(default_factory=list)
    asset_bindings: List[AssetBinding] = Field(default_factory=list)
    deliverable_metadata: DeliverableMeta = Field(default_factory=DeliverableMeta)
    interpretation_notes: str = ""

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict) -> "InterpretedIntake":
        return cls.model_validate(data)

    def public_dict(self) -> dict:
        """Safe for reports/UI — no secret field values."""
        data = self.to_dict()
        for asset in data.get("assets", []):
            asset["fields"] = {
                k: "[STORED]" if k in asset.get("sensitive_field_keys", []) else v
                for k, v in (asset.get("fields") or {}).items()
            }
        return data
