"""Application errors."""


class AgentResponseError(RuntimeError):
    """LLM response could not be parsed or was empty under real-only policy."""


class IntakeGuardrailError(RuntimeError):
    """Raised when intake fails guardrail validation."""

    def __init__(self, message: str, result=None):
        super().__init__(message)
        self.result = result
