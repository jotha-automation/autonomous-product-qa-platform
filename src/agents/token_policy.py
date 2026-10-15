"""Central LLM token / prompt budget policy — proportional to intake and expected output."""
from __future__ import annotations

import os
from typing import Any, Optional

from src.config import get_settings


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def max_prompt_chars() -> int:
    """Hard ceiling for a single prompt (characters)."""
    return _env_int("APTP_MAX_PROMPT_CHARS", 250_000)


def default_prompt_chars() -> int:
    """Default prompt budget when payload is small."""
    return max(get_settings().aptp_default_prompt_chars, _env_int("APTP_DEFAULT_PROMPT_CHARS", 64_000))


def max_output_tokens() -> int:
    """Hard ceiling for completion tokens per LLM call."""
    return _env_int("APTP_MAX_OUTPUT_TOKENS", 16_384)


def min_output_tokens() -> int:
    return _env_int("APTP_MIN_OUTPUT_TOKENS", 4_096)


def output_tokens_ceiling() -> int:
    return _env_int("APTP_MAX_OUTPUT_TOKENS_CEILING", 32_768)


def codegen_output_tokens_ceiling() -> int:
    """Higher completion ceiling for codegen agents so large Playwright projects aren't clamped."""
    return _env_int("APTP_CODEGEN_MAX_OUTPUT_TOKENS_CEILING", 65_536)


def output_tokens_ceiling_for(agent_name: str) -> int:
    """Profile-aware ceiling: codegen agents use the codegen ceiling, others the global one."""
    try:
        from src.services.llm_routing import LLMProfile, profile_for_agent

        if profile_for_agent(agent_name) == LLMProfile.CODEGEN:
            return codegen_output_tokens_ceiling()
    except Exception:
        pass
    return output_tokens_ceiling()


def reasoning_max_output_tokens() -> int:
    """gpt-4.1 and most reasoning deployments cap at 32k completion tokens."""
    return _env_int("APTP_REASONING_MAX_OUTPUT_TOKENS", 32_768)


def codegen_max_output_tokens() -> int:
    return _env_int("APTP_CODEGEN_MAX_OUTPUT_TOKENS", 65_536)


def model_max_completion_tokens(agent_name: str) -> int:
    try:
        from src.services.llm_routing import LLMProfile, profile_for_agent

        if profile_for_agent(agent_name) == LLMProfile.CODEGEN:
            return codegen_max_output_tokens()
    except Exception:
        pass
    return reasoning_max_output_tokens()


def intake_doc_char_limit() -> int:
    """Max characters per intake document embedded in a prompt."""
    return _env_int("APTP_INTAKE_MAX_DOC_CHARS", 120_000)


def intake_max_documents() -> int:
    return _env_int("APTP_INTAKE_MAX_DOCUMENTS", 24)


# Optional per-agent output floors (completion tokens). None → use global policy only.
AGENT_OUTPUT_FLOORS: dict[str, int] = {
    "IntakeInterpretationAgent": 16_384,
    "RequirementsAnalystAgent": 16_384,
    "TestDesignAgent": 16_384,
    "PlaywrightArchitectAgent": 24_576,
    "PlaywrightGeneratorAgent": 32_768,
    "FailureRCAAgent": 8_192,
    "SelfHealingAgent": 8_192,
    "DefectDetectionAgent": 8_192,
    "ExecutiveReportingAgent": 8_192,
    "ClientIntakeInterpreterAgent": 16_384,
    "QaObligationExtractorAgent": 16_384,
}


def prompt_budget(agent_name: str, content_chars: int = 0, *, explicit: Optional[int] = None) -> int:
    """
    Prompt char budget proportional to payload size, bounded by global ceiling.
    Small payloads are not padded; large payloads use up to max_prompt_chars().
    """
    if explicit is not None and explicit > 0:
        return min(max_prompt_chars(), explicit)
    from src.agents.prompts.enterprise import AGENT_PROMPT_BUDGETS

    agent_floor = AGENT_PROMPT_BUDGETS.get(agent_name, 0)
    needed = max(content_chars, agent_floor, default_prompt_chars())
    return min(max_prompt_chars(), needed)


def output_tokens(agent_name: str, *, item_count: int = 0, chars_hint: int = 0) -> int:
    """
    Completion token budget proportional to expected output volume.
    Capped per model profile (reasoning 32k, codegen higher).
    """
    from src.agents.prompts.enterprise import AGENT_MAX_TOKENS

    configured = AGENT_MAX_TOKENS.get(agent_name)
    floor = AGENT_OUTPUT_FLOORS.get(agent_name, min_output_tokens())
    base = configured if configured else max_output_tokens()
    base = max(base, floor, min_output_tokens())

    if item_count > 0:
        per_item = _env_int("APTP_OUTPUT_TOKENS_PER_ITEM", 96)
        incremental_cap = _env_int("APTP_OUTPUT_TOKENS_INCREMENTAL_CAP", 24_000)
        incremental = min(item_count * per_item, incremental_cap)
        base = max(base, min_output_tokens() + incremental)
    if chars_hint > 0:
        base = max(base, min_output_tokens() + chars_hint // 4)

    model_cap = model_max_completion_tokens(agent_name)
    global_cap = min(output_tokens_ceiling_for(agent_name), model_cap)
    return min(global_cap, base)


def json_for_prompt(data: Any, *, agent_name: str = "", max_chars: Optional[int] = None) -> str:
    """Serialize JSON for prompts; truncate only when exceeding proportional budget.

    Truncation happens at the last valid JSON boundary (object/array/string) so the
    LLM never sees a malformed mid-object fragment. Spanish and other Unicode text
    is preserved as-is (ensure_ascii=False).
    """
    import json

    text = json.dumps(data, default=str, ensure_ascii=False)
    budget = max_chars if max_chars is not None else prompt_budget(agent_name, len(text))
    if len(text) <= budget:
        return text
    return _truncate_json_at_boundary(text, budget)


def _truncate_json_at_boundary(text: str, budget: int) -> str:
    """Return the longest valid-JSON prefix up to *budget* plus a closing suffix."""
    import json

    hi = min(budget, len(text))
    best = ""
    lo = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid]
        try:
            json.loads(candidate)
            best = candidate
            lo = mid + 1
        except json.JSONDecodeError:
            hi = mid - 1
    if best:
        return f"{best} ... [truncated {len(text)} chars to budget {budget}]"
    return f"{text[:budget]}... [truncated {len(text)} chars to budget {budget}]"


def truncate_text(text: str, *, agent_name: str = "", max_chars: Optional[int] = None) -> str:
    if not text:
        return text
    budget = max_chars if max_chars is not None else prompt_budget(agent_name, len(text))
    if len(text) <= budget:
        return text
    return f"{text[:budget]}\n\n[TRUNCATED: {len(text)} chars to budget {budget}]"
