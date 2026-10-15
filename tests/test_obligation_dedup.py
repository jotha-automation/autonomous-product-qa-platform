from src.automation.obligation_dedup import dedupe_obligations


def test_dedupe_obligations_by_external_id():
    obligations = [
        {"id": "OBL-1", "external_id": "EMPRESARIAL-13330", "automation_tier": "partial"},
        {"id": "OBL-2", "external_id": "EMPRESARIAL-13330", "automation_tier": "automated"},
        {"id": "OBL-3", "external_id": "EMPRESARIAL-13331", "automation_tier": "automated"},
    ]
    deduped = dedupe_obligations(obligations)
    assert len(deduped) == 2
    assert deduped[0]["automation_tier"] == "automated"
    assert deduped[0]["id"] == "OBL-2"
