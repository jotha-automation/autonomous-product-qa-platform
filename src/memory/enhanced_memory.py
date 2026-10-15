"""Enhanced memory with execution namespaces."""
import json
import gzip
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from enum import Enum

try:
    import redis
except ImportError:
    redis = None  # type: ignore

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None  # type: ignore[misc, assignment]
    InvalidToken = Exception  # type: ignore[misc, assignment]

logger = logging.getLogger("enhanced_memory")

_RUN_SECRET_PREFIX = "run_secret:"
_ENCRYPTED_MARKER = "__aptp_encrypted__"
_redis_fallback_warned = False


class MemoryTier(Enum):
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    EPHEMERAL = "ephemeral"


@dataclass
class MemoryEntry:
    key: str
    value: Any
    tier: MemoryTier
    created_at: datetime
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    compressed: bool = False
    size_bytes: int = 0
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class EnhancedSharedMemory:
    TTL_MAP = {
        MemoryTier.WORKING: None,
        MemoryTier.SHORT_TERM: 86400,
        MemoryTier.LONG_TERM: 604800,
        MemoryTier.EPHEMERAL: 3600,
    }
    COMPRESSION_THRESHOLD = 1024

    EXECUTION_NAMESPACES = (
        "test_plan",
        "generated_code",
        "execution_results",
        "rca",
        "healing",
    )

    def __init__(self, redis_url: Optional[str] = None):
        global _redis_fallback_warned
        from src.runtime_policy import RedisRequiredError, require_redis

        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._memory: Dict[str, bytes] = {}
        self._available = False
        self.client = None
        self._fernet: Optional[Any] = None
        self._redis_error: Optional[str] = None

        if redis is not None:
            try:
                self.client = redis.from_url(redis_url, decode_responses=False)
                self.client.ping()
                self._available = True
            except Exception as exc:
                self._redis_error = str(exc)
                if require_redis():
                    raise RedisRequiredError(
                        f"APTP_REQUIRE_REDIS=true but Redis connection failed ({redis_url}): {exc}"
                    ) from exc
                if not _redis_fallback_warned:
                    logger.warning(
                        "Redis unavailable at %s — using in-process memory fallback (%s)",
                        redis_url,
                        exc,
                    )
                    _redis_fallback_warned = True

        self._stats = {"writes": 0, "reads": 0, "compressions": 0, "decompressions": 0}

    def is_redis_available(self) -> bool:
        return self._available

    def redis_health(self) -> Dict[str, Any]:
        from src.runtime_policy import require_redis

        if self._available:
            return {"status": "available", "backend": "redis"}
        if require_redis():
            return {
                "status": "required_missing",
                "backend": "in-memory",
                "error": self._redis_error,
            }
        return {
            "status": "unavailable",
            "backend": "in-memory",
            "error": self._redis_error,
        }

    @staticmethod
    def _is_run_secret_key(key: str) -> bool:
        return key.startswith(_RUN_SECRET_PREFIX)

    @staticmethod
    def run_secret_ttl_seconds() -> int:
        raw = os.getenv("APTP_RUN_SECRET_TTL_SECONDS", "3600").strip()
        try:
            return max(60, int(raw))
        except ValueError:
            return 3600

    def _get_fernet(self) -> Optional[Any]:
        if Fernet is None:
            logger.warning("cryptography package missing — run secrets stored without encryption")
            return None
        if self._fernet is None:
            from src.services.secrets import get_secrets_service

            self._fernet = Fernet(get_secrets_service().get_encryption_key())
        return self._fernet

    def _encrypt_run_secret_value(self, value: Any) -> Any:
        fernet = self._get_fernet()
        if fernet is None:
            return value
        plaintext = json.dumps(value, default=str).encode("utf-8")
        token = fernet.encrypt(plaintext).decode("ascii")
        return {_ENCRYPTED_MARKER: True, "ciphertext": token}

    def _decrypt_run_secret_value(self, value: Any) -> Any:
        if not isinstance(value, dict) or not value.get(_ENCRYPTED_MARKER):
            return value
        fernet = self._get_fernet()
        if fernet is None:
            return value
        ciphertext = value.get("ciphertext")
        if not isinstance(ciphertext, str):
            return value
        try:
            plaintext = fernet.decrypt(ciphertext.encode("ascii"))
            return json.loads(plaintext.decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to decrypt run secret payload: %s", exc)
            return value

    @staticmethod
    def scoped_key(scope: Optional[str], key: str) -> str:
        normalized_scope = (scope or "").strip(":")
        if not normalized_scope:
            return key
        if key.startswith(f"{normalized_scope}:"):
            return key
        return f"{normalized_scope}:{key}"

    @staticmethod
    def execution_key(execution_id: str, namespace: str) -> str:
        """Build execution:{id}:{namespace} key pattern."""
        return f"execution:{execution_id}:{namespace}"

    @property
    def available(self) -> bool:
        return self._available

    def write(
        self,
        key: str,
        value: Any,
        tier: MemoryTier = MemoryTier.SHORT_TERM,
        tags: Optional[List[str]] = None,
        compress: Optional[bool] = None,
    ) -> None:
        if self._is_run_secret_key(key):
            tier = MemoryTier.SHORT_TERM
            value = self._encrypt_run_secret_value(value)

        serialized = json.dumps(value, default=str)
        data_bytes = serialized.encode("utf-8")
        should_compress = compress if compress is not None else len(data_bytes) > self.COMPRESSION_THRESHOLD
        if should_compress:
            data_bytes = gzip.compress(data_bytes)
            self._stats["compressions"] += 1

        entry = MemoryEntry(
            key=key, value=None, tier=tier, created_at=datetime.utcnow(),
            compressed=should_compress, size_bytes=len(data_bytes), tags=tags or [],
        )
        metadata = json.dumps(asdict(entry), default=str)
        ttl = self.run_secret_ttl_seconds() if self._is_run_secret_key(key) else self.TTL_MAP[tier]
        data_key, meta_key = f"data:{key}", f"meta:{key}"

        if self._available and self.client:
            pipe = self.client.pipeline()
            pipe.set(data_key, data_bytes, ex=ttl)
            pipe.set(meta_key, metadata, ex=ttl)
            pipe.execute()
        else:
            self._memory[data_key] = data_bytes
            self._memory[meta_key] = metadata.encode()
        self._stats["writes"] += 1

    def write_many(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            tier = entry.get("tier", MemoryTier.SHORT_TERM)
            if isinstance(tier, str):
                tier = MemoryTier(tier)
            self.write(entry["key"], entry.get("value"), tier=tier, tags=entry.get("tags"))

    def read(self, key: str, update_access: bool = True) -> Optional[Any]:
        data_key = f"data:{key}"
        data = self.client.get(data_key) if self._available and self.client else self._memory.get(data_key)
        if data is None:
            return None

        meta_key = f"meta:{key}"
        meta_raw = self.client.get(meta_key) if self._available and self.client else self._memory.get(meta_key)
        compressed = False
        if meta_raw:
            try:
                meta = json.loads(meta_raw.decode() if isinstance(meta_raw, bytes) else meta_raw)
                compressed = meta.get("compressed", False)
            except Exception:
                pass

        if compressed:
            data = gzip.decompress(data)
            self._stats["decompressions"] += 1
        self._stats["reads"] += 1
        value = json.loads(data.decode("utf-8"))
        if self._is_run_secret_key(key):
            return self._decrypt_run_secret_value(value)
        return value

    def read_many(self, keys: List[str], update_access: bool = True) -> Dict[str, Any]:
        return {k: v for k in keys if (v := self.read(k, update_access)) is not None}

    def read_with_fallback(self, key: str, fallback_keys: Optional[List[str]] = None, update_access: bool = True) -> Optional[Any]:
        val = self.read(key, update_access)
        if val is not None:
            return val
        for fb in fallback_keys or []:
            val = self.read(fb, update_access)
            if val is not None:
                return val
        return None

    def delete(self, key: str) -> bool:
        data_key, meta_key = f"data:{key}", f"meta:{key}"
        if self._available and self.client:
            return bool(self.client.delete(data_key, meta_key))
        found = data_key in self._memory
        self._memory.pop(data_key, None)
        self._memory.pop(meta_key, None)
        return found

    def exists(self, key: str) -> bool:
        if self._available and self.client:
            return self.client.exists(f"data:{key}") > 0
        return f"data:{key}" in self._memory

    def get_context_window(self, prefix: str, max_items: int = 10, include_metadata: bool = False) -> List[Any]:
        keys = [k.replace("meta:", "") for k in self._memory if k.startswith(f"meta:{prefix}")]
        if self._available and self.client:
            keys = [k.decode().replace("meta:", "") for k in self.client.scan_iter(match=f"meta:{prefix}*")]
        result = []
        for key in keys[:max_items]:
            val = self.read(key, update_access=False)
            if val is not None:
                result.append(val)
        return result

    def list_keys_with_prefix(self, prefix: str, limit: int = 50) -> List[str]:
        """List metadata keys matching meta:{prefix}* (best-effort)."""
        found: List[str] = []
        if self._available and self.client:
            for raw in self.client.scan_iter(match=f"meta:{prefix}*", count=100):
                key = raw.decode().replace("meta:", "") if isinstance(raw, bytes) else str(raw).replace("meta:", "")
                found.append(key)
                if len(found) >= limit:
                    break
        else:
            for k in self._memory:
                if k.startswith(f"meta:{prefix}"):
                    found.append(k.replace("meta:", ""))
                    if len(found) >= limit:
                        break
        return found

    def read_scoped_bundle(self, scope: str, keys: List[str]) -> Dict[str, Any]:
        """Read multiple scoped artifacts in one round-trip."""
        out: Dict[str, Any] = {}
        for key in keys:
            scoped = self.scoped_key(scope, key)
            val = self.read_with_fallback(scoped, fallback_keys=[key])
            if val is not None:
                out[key] = val
        return out

    def get_stats(self) -> Dict[str, Any]:
        stats = dict(self._stats)
        stats["backend"] = "redis" if self._available else "in-memory"
        return stats


_memory_instance: Optional[EnhancedSharedMemory] = None


def get_memory() -> EnhancedSharedMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = EnhancedSharedMemory()
    return _memory_instance
