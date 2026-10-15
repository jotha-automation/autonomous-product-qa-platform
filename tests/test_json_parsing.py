from src.agents.base import BaseAgent


class _Agent(BaseAgent):
    pass


def test_parse_json_response_strips_markdown_fence():
    agent = _Agent.__new__(_Agent)
    raw = '```json\n{"pages": {}, "specs": {}}\n```'
    parsed = agent.parse_json_response(raw, None)
    assert parsed == {"pages": {}, "specs": {}}


def test_parse_json_response_repairs_trailing_commas():
    agent = _Agent.__new__(_Agent)
    raw = '{"pages": {"HomePage": {"methods": [],},}, "specs": {},}'
    parsed = agent.parse_json_response(raw, None)
    assert parsed["pages"]["HomePage"]["methods"] == []
