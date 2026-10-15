from src.agents.token_policy import json_for_prompt, output_tokens, prompt_budget


def test_prompt_budget_scales_with_content():
    small = prompt_budget("TestDesignAgent", 5_000)
    large = prompt_budget("TestDesignAgent", 200_000)
    assert small >= 5_000
    assert large >= 200_000
    assert large >= small


def test_output_tokens_scales_with_items():
    base = output_tokens("TestDesignAgent")
    scaled = output_tokens("TestDesignAgent", item_count=40)
    assert scaled >= base


def test_output_tokens_respects_gpt41_limit_for_requirements():
    # 32 obligations — must not exceed reasoning model cap (32k)
    tokens = output_tokens("RequirementsAnalystAgent", item_count=32)
    assert tokens <= 32_768
    # Legacy over-scaled item_count (obligations * 4) must also stay bounded
    tokens_legacy = output_tokens("RequirementsAnalystAgent", item_count=128)
    assert tokens_legacy <= 32_768


def test_codegen_agent_allows_higher_ceiling(monkeypatch):
    monkeypatch.setenv("APTP_CODEGEN_MAX_OUTPUT_TOKENS", "65536")
    tokens = output_tokens("PlaywrightGeneratorAgent", item_count=40)
    assert tokens <= 65_536
    assert tokens >= 16_384


def test_json_for_prompt_no_truncation_when_small():
    payload = {"requirements": [{"id": "REQ-1", "description": "Hero"}]}
    text = json_for_prompt(payload, agent_name="CoverageAgent")
    assert "REQ-1" in text
    assert "truncated" not in text
