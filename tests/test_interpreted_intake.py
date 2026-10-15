"""Tests for interpreted intake schema."""
from src.intake.schemas.interpreted import InterpretedIntake, TestObligation, TestAsset, SourceRef


def test_interpreted_intake_roundtrip():
    intake = InterpretedIntake(
        intake_mode="content_first",
        confidence=0.8,
        obligations=[
            TestObligation(
                id="OBL-001",
                title="Login portal",
                steps=["Open URL", "Enter credentials"],
                automation_tier="automated",
                required_assets=["login_user"],
            )
        ],
        assets=[
            TestAsset(
                key="login_user",
                asset_type="credential",
                source_ref=SourceRef(filename="users.xlsx", sheet="Usuarios"),
                fields={"usuario": "login_user.usuario"},
                sensitive=True,
            )
        ],
    )
    data = intake.to_dict()
    restored = InterpretedIntake.from_dict(data)
    assert restored.obligations[0].id == "OBL-001"
    assert restored.assets[0].key == "login_user"
    assert restored.intake_mode == "content_first"
