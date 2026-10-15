"""Run-scoped secret storage — credentials never appear in reports or logs."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from src.memory.enhanced_memory import MemoryTier, get_memory

logger = logging.getLogger("run_secret_store")

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|clave|token|secret|otp|pin|credential|api[_-]?key|numdocumento)",
    re.I,
)


class RunSecretStore:
    """In-memory (Redis-backed) secret vault scoped per run."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._prefix = f"run_secret:{run_id}:"
        self._memory = get_memory()
        self._registered_keys: Set[str] = set()

    def register(self, key: str, value: str, *, source: str = "") -> None:
        normalized = self._normalize_key(key)
        self._memory.write(
            self._prefix + normalized,
            {"value": value, "source": source},
            tier=MemoryTier.SHORT_TERM,
            tags=["secret", f"run:{self.run_id}"],
        )
        self._registered_keys.add(normalized)
        logger.info("Registered secret key %s for run %s (source=%s)", normalized, self.run_id, source)
        self._persist_key_list()

    def get(self, key: str) -> Optional[str]:
        normalized = self._normalize_key(key)
        payload = self._memory.read(self._prefix + normalized)
        if isinstance(payload, dict):
            return payload.get("value")
        return None

    def list_keys(self) -> List[str]:
        audit = self._memory.read(self._prefix + "__keys__")
        if isinstance(audit, list):
            keys = list(set(self._registered_keys) | set(audit))
        else:
            keys = list(self._registered_keys)
        return sorted(keys)

    def _persist_key_list(self) -> None:
        self._memory.write(self._prefix + "__keys__", sorted(self._registered_keys), tier=MemoryTier.SHORT_TERM)

    def register_batch(self, secrets: Dict[str, str], *, source: str = "") -> List[str]:
        registered = []
        for key, value in secrets.items():
            if value:
                self.register(key, value, source=source)
                registered.append(self._normalize_key(key))
        self._persist_key_list()
        return registered

    def redact_text(self, text: str) -> str:
        """Replace known secret values with [REDACTED]."""
        result = text
        for key in self.list_keys():
            val = self.get(key)
            if val and len(val) > 2 and val in result:
                result = result.replace(val, "[REDACTED]")
        return result

    def redact_dict(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: self._redact_value(k, v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.redact_dict(item) for item in data]
        return data

    def _redact_value(self, key: str, value: Any) -> Any:
        if SENSITIVE_KEY_PATTERN.search(str(key)):
            return "[REDACTED]" if value else value
        if isinstance(value, str):
            return self.redact_text(value)
        return self.redact_dict(value)

    def audit_log(self) -> List[Dict[str, str]]:
        return [{"key": k, "registered": True} for k in self.list_keys()]

    @staticmethod
    def _normalize_key(key: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-]", "_", key.strip().lower())

    @classmethod
    def for_run(cls, run_id: str) -> "RunSecretStore":
        return cls(run_id)


def get_run_secret_store(run_id: str) -> RunSecretStore:
    return RunSecretStore.for_run(run_id)


def is_sensitive_field_name(name: str) -> bool:
    return bool(SENSITIVE_KEY_PATTERN.search(name or ""))


def identify_sensitive_columns(headers: list[str]) -> list[str]:
    return [h for h in headers if is_sensitive_field_name(str(h))]
