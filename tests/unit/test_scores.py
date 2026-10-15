"""Tests for score coercion and release readiness."""
from src.reporting.builders import compute_release_readiness_score
from src.reporting.numeric import coerce_score_float


def test_coerce_score_float_list_averages():
    assert coerce_score_float([0.2, 0.4]) == 0.3


def test_coerce_score_float_nested_dict():
    assert coerce_score_float({"functional": [0.1, 0.3]}) == 0.2


def test_release_readiness_score_tolerates_list_risk_scores():
    score = compute_release_readiness_score(
        {"coverage_score": 0.8},
        {"risk_scores": {"functional": [0.2, 0.4]}},
        [{"coverage": "full"}],
        {"score": [0.7, 0.9]},
        execution={"total": 10, "failed": 0, "pass_rate": 1.0},
    )
    assert score > 0


def test_release_readiness_score_uses_obligation_coverage_list():
    score = compute_release_readiness_score(
        {
            "coverage_score": 0,
            "obligation_coverage": [
                {"obligation_id": "OBL-1", "covered": True},
                {"obligation_id": "OBL-2", "covered": False},
            ],
        },
        {"release_risk_score": 0.2},
        [{"coverage": "full"}],
        {"score": 0.9},
    )
    assert score > 0


def test_release_readiness_score_reflects_partial_execution():
    score = compute_release_readiness_score(
        {"coverage_score": 0},
        {"release_risk_score": 1.0},
        [],
        {"score": 0.95},
        execution={"total": 103, "failed": 10, "pass_rate": 0.9029126213592233},
    )
    assert score >= 70.0


def test_build_qa_report_includes_obligation_count():
    from src.reporting.builders import build_qa_report

    qa = build_qa_report(
        {
            "interpreted_intake": {
                "obligations": [{"id": "OBL-1", "title": "Login"}],
                "intake_mode": "content_first",
            },
            "readiness": {
                "obligation_count": 1,
                "intake_profile": {"primary_strategy": "interpretation"},
            },
        }
    )
    assert qa["interpreted_intake"]["obligation_count"] == 1
    assert qa["interpreted_intake"]["intake_profile"]["primary_strategy"] == "interpretation"
