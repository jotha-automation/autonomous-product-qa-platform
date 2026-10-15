import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _env_defaults():
    os.environ.setdefault("APTP_ALLOW_MOCKS", "true")
    os.environ.setdefault("APTP_USE_MOCK_LLM", "true")
    os.environ.setdefault("APTP_SKIP_PLAYWRIGHT_INSTALL", "true")
    os.environ.setdefault("APTP_MOCK_PLAYWRIGHT_EXECUTION", "true")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("APTP_ALLOW_DEMO_SECRETS", "true")
    os.environ.setdefault("APTP_DEMO_LOGIN_USER_CLIENTE", "312")
    os.environ.setdefault("APTP_DEMO_LOGIN_USER_TIPO_ID", "CC")
    os.environ.setdefault("APTP_DEMO_LOGIN_USER_DOCUMENTO", "20242587")
    os.environ.setdefault("APTP_DEMO_LOGIN_USER_CLAVE", "123456")
    os.environ.setdefault("APTP_DEMO_LOGIN_USER_TOKEN", "63866019")
    os.environ.setdefault("APTP_DEMO_NO_TOKEN_CLIENTE", "201319")
    os.environ.setdefault("APTP_DEMO_NO_TOKEN_TIPO_ID", "CC")
    os.environ.setdefault("APTP_DEMO_NO_TOKEN_DOCUMENTO", "1233896904")
    os.environ.setdefault("APTP_DEMO_NO_TOKEN_CLAVE", "2023")
    os.environ.setdefault("APTP_DEMO_NO_TOKEN_TOKEN", "")
    os.environ.setdefault("APTP_SECRETS_ENCRYPTION_KEY", "test-encryption-key")
    os.environ.setdefault("APTP_API_KEY", "test-api-key")
