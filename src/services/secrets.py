"""Secrets service — provider abstraction (env / Azure Key Vault) + run-scoped secrets."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

from src.services.run_secret_store import RunSecretStore

logger = logging.getLogger("secrets")

_AZURE_KV_IMPORT_ERROR: Optional[str] = None

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    _AZURE_KV_AVAILABLE = True
except ImportError as exc:
    DefaultAzureCredential = None  # type: ignore[misc, assignment]
    SecretClient = None  # type: ignore[misc, assignment]
    _AZURE_KV_AVAILABLE = False
    _AZURE_KV_IMPORT_ERROR = str(exc)


def _normalize_secret_name(name: str) -> str:
    return name.upper().replace("-", "_")


def fernet_key_from_secret(secret: str) -> bytes:
    """Derive a url-safe 32-byte Fernet key from arbitrary secret material."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecretsProvider(ABC):
    @abstractmethod
    def get_secret(self, name: str) -> str:
        """Return a named secret value (empty string when unset)."""

    @abstractmethod
    def get_encryption_key(self) -> bytes:
        """Return Fernet-compatible encryption key bytes."""


class EnvSecretsProvider(SecretsProvider):
    """Default provider — reads secrets from process environment."""

    def get_secret(self, name: str) -> str:
        return os.getenv(_normalize_secret_name(name), "")

    def get_encryption_key(self) -> bytes:
        explicit = os.getenv("APTP_SECRETS_ENCRYPTION_KEY", "").strip()
        if explicit:
            return fernet_key_from_secret(explicit)
        fallback = os.getenv("APTP_API_KEY", "").strip() or "aptp-dev-encryption-key"
        return fernet_key_from_secret(fallback)


class AzureKeyVaultProvider(SecretsProvider):
    """Production provider — Azure Key Vault via DefaultAzureCredential."""

    def __init__(self, vault_url: str):
        if not _AZURE_KV_AVAILABLE:
            raise ImportError(
                "APTP_SECRETS_PROVIDER=azure_key_vault requires azure-identity and "
                f"azure-keyvault-secrets packages ({_AZURE_KV_IMPORT_ERROR})"
            )
        self._vault_url = vault_url.rstrip("/")
        credential = DefaultAzureCredential()
        self._client = SecretClient(vault_url=self._vault_url, credential=credential)
        self._cache: dict[str, str] = {}

    def get_secret(self, name: str) -> str:
        vault_name = name.replace("_", "-").lower()
        if vault_name in self._cache:
            return self._cache[vault_name]
        try:
            bundle = self._client.get_secret(vault_name)
            value = bundle.value or ""
        except Exception as exc:
            logger.warning("Key Vault lookup failed for %s: %s", vault_name, exc)
            value = ""
        self._cache[vault_name] = value
        return value

    def get_encryption_key(self) -> bytes:
        key_material = self.get_secret("aptp-secrets-encryption-key")
        if not key_material:
            key_material = self.get_secret("APTP_SECRETS_ENCRYPTION_KEY")
        if not key_material:
            raise RuntimeError(
                "Azure Key Vault provider requires secret 'aptp-secrets-encryption-key' "
                f"(vault {self._vault_url})"
            )
        return fernet_key_from_secret(key_material)


def get_secrets_provider() -> SecretsProvider:
    provider_name = os.getenv("APTP_SECRETS_PROVIDER", "env").strip().lower()
    if provider_name in {"azure_key_vault", "azure", "keyvault"}:
        vault_url = os.getenv("AZURE_KEY_VAULT_URL", "").strip()
        if not vault_url:
            raise RuntimeError(
                "APTP_SECRETS_PROVIDER=azure_key_vault requires AZURE_KEY_VAULT_URL"
            )
        return AzureKeyVaultProvider(vault_url)
    return EnvSecretsProvider()


class SecretsService:
    def __init__(self, provider: Optional[SecretsProvider] = None):
        self._provider = provider or get_secrets_provider()

    def get_secret(self, name: str) -> str:
        env_value = os.getenv(_normalize_secret_name(name), "")
        if env_value:
            return env_value
        return self._provider.get_secret(name)

    def get_encryption_key(self) -> bytes:
        return self._provider.get_encryption_key()

    def get_runtime_secret(self, run_id: str, key: str, default: str = "") -> str:
        return RunSecretStore.for_run(run_id).get(key) or default

    def register_runtime_secret(
        self, run_id: str, key: str, value: str, *, source: str = ""
    ) -> None:
        RunSecretStore.for_run(run_id).register(key, value, source=source)


_secrets_service: Optional[SecretsService] = None


def get_secrets_service() -> SecretsService:
    global _secrets_service
    if _secrets_service is None:
        _secrets_service = SecretsService()
    return _secrets_service


def reset_secrets_service() -> None:
    """Test helper — clear cached singleton."""
    global _secrets_service
    _secrets_service = None
