import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://aptp:aptp@localhost:5432/aptp"
    aptp_api_key: str = ""
    aptp_env: str = "development"
    aptp_allow_open_api: bool = False
    aptp_run_access_token_secret: str = ""
    aptp_run_access_token_ttl_seconds: int = 3600
    aptp_use_mock_llm: bool = False
    aptp_skip_playwright_install: bool = False
    aptp_mock_playwright_execution: bool = False
    aptp_allow_mocks: bool = False
    max_heal_attempts: int = 2
    heal_confidence_threshold: float = 0.6
    playwright_projects_root: str = "generated-test-projects"
    service_bus_queue_name: str = "aptp-events"
    aptp_default_prompt_chars: int = 64000
    aptp_max_prompt_chars: int = 250000
    aptp_default_max_output_tokens: int = 16384
    aptp_intake_max_doc_chars: int = 120000

    class Config:
        env_file = ".env"
        extra = "ignore"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            database_url=os.getenv(
                "DATABASE_URL", "postgresql+asyncpg://aptp:aptp@localhost:5432/aptp"
            ),
            aptp_api_key=(os.getenv("APTP_API_KEY") or "").strip(),
            aptp_env=(os.getenv("APTP_ENV") or os.getenv("RAILWAY_ENVIRONMENT") or "development").strip(),
            aptp_allow_open_api=os.getenv("APTP_ALLOW_OPEN_API", "false").lower()
            in ("1", "true", "yes"),
            aptp_run_access_token_secret=(os.getenv("APTP_RUN_ACCESS_TOKEN_SECRET") or "").strip(),
            aptp_run_access_token_ttl_seconds=int(
                os.getenv("APTP_RUN_ACCESS_TOKEN_TTL_SECONDS", "3600")
            ),
            aptp_allow_mocks=os.getenv("APTP_ALLOW_MOCKS", "false").lower()
            in ("1", "true", "yes"),
            aptp_use_mock_llm=os.getenv("APTP_USE_MOCK_LLM", "false").lower()
            in ("1", "true", "yes"),
            aptp_skip_playwright_install=os.getenv(
                "APTP_SKIP_PLAYWRIGHT_INSTALL", "false"
            ).lower()
            in ("1", "true", "yes"),
            aptp_mock_playwright_execution=os.getenv(
                "APTP_MOCK_PLAYWRIGHT_EXECUTION", "false"
            ).lower()
            in ("1", "true", "yes"),
            max_heal_attempts=int(os.getenv("MAX_HEAL_ATTEMPTS", "2")),
            heal_confidence_threshold=float(os.getenv("HEAL_CONFIDENCE_THRESHOLD", "0.6")),
            playwright_projects_root=os.getenv(
                "PLAYWRIGHT_PROJECTS_ROOT", "generated-test-projects"
            ),
            service_bus_queue_name=os.getenv("SERVICE_BUS_QUEUE_NAME", "aptp-events"),
            aptp_default_prompt_chars=int(os.getenv("APTP_DEFAULT_PROMPT_CHARS", "64000")),
            aptp_max_prompt_chars=int(os.getenv("APTP_MAX_PROMPT_CHARS", "250000")),
            aptp_default_max_output_tokens=int(os.getenv("APTP_DEFAULT_MAX_OUTPUT_TOKENS", "16384")),
            aptp_intake_max_doc_chars=int(os.getenv("APTP_INTAKE_MAX_DOC_CHARS", "120000")),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
