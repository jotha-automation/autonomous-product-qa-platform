"""E2E fixtures: FastAPI client, auth headers, fixture documents."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
API_KEY = "test-e2e-api-key"


@pytest.fixture(scope="session", autouse=True)
def _e2e_env():
    os.environ["APTP_ALLOW_MOCKS"] = "true"
    os.environ["APTP_USE_MOCK_LLM"] = "true"
    os.environ["APTP_SKIP_PLAYWRIGHT_INSTALL"] = "true"
    os.environ["APTP_MOCK_PLAYWRIGHT_EXECUTION"] = "true"
    os.environ["APTP_API_KEY"] = API_KEY
    os.environ["GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID"] = "mock-aptp"
    os.environ["APTP_DISABLE_INTAKE_DB"] = "true"
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-Api-Key": API_KEY}


@pytest.fixture
async def api_client() -> AsyncIterator[AsyncClient]:
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def portfolio_documents() -> list[dict]:
    docs = []
    mapping = [
        ("BRD", "brd.txt"),
        ("PRD", "prd.txt"),
        ("UC", "uc.txt"),
        ("AC", "ac.txt"),
    ]
    for doc_type, filename in mapping:
        path = FIXTURES / filename
        text = path.read_text(encoding="utf-8") if path.is_file() else f"{doc_type} sample content for QA pipeline."
        docs.append(
            {
                "id": filename,
                "filename": filename,
                "document_type": doc_type,
                "raw_text": text,
                "extra": {"intake_role": "PRIMARY_SPEC" if doc_type == "BRD" else "SUPPLEMENTARY"},
            }
        )
    return docs


@pytest.fixture
def unstructured_qa_document() -> dict:
    return {
        "id": "qa-spec.pdf",
        "filename": "qa-spec.pdf",
        "document_type": "OTHER",
        "raw_text": (
            "REQ INC1353166 - Portal Empresarial Inscripciones\n"
            "Caso de Prueba EMPRESARIAL-13327: Parámetro = 1 día Hábil\n"
            "Pasos:\n1. Ingresar al módulo de Inscripciones\n"
            "Resultados Esperados:\n1. Se visualiza pantalla Crear Inscripción\n"
            "Precondiciones: Usuario con acceso al Portal Empresarial\n"
        ),
        "extra": {"intake_role": "PRIMARY_SPEC", "pages": [{"page": 1, "text": "EMPRESARIAL-13327"}]},
    }


@pytest.fixture
def credential_excel_document() -> dict:
    return {
        "id": "test-data.xlsx",
        "filename": "Formato Inscripciones.xlsx",
        "document_type": "OTHER",
        "raw_text": "",
        "extra": {
            "intake_role": "ASSET_BUNDLE",
            "sheets": [
                {
                    "name": "Usuarios",
                    "headers": ["cliente", "tipoId", "numDocumento", "clave", "token"],
                    "rows": [
                        ["cliente", "tipoId", "numDocumento", "clave", "token"],
                        ["312", "Cédula", "20242587", "123456", "63866019"],
                    ],
                }
            ],
        },
    }


@pytest.fixture
def unique_project_name() -> str:
    return f"APTP-E2E-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def require_postgres_for_drive():
    """Drive project API persists registry state — skip when Postgres is down."""
    try:
        from sqlalchemy import text
        from src.services.postgres import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres unavailable — skip Drive/DB e2e")

@pytest.fixture
def require_postgres_for_drive_sync():
    """Sync variant for tests that use Starlette TestClient."""
    import asyncio

    from src.services import postgres as postgres_mod

    async def _check():
        from sqlalchemy import text

        factory = postgres_mod.get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))

    async def _dispose():
        if postgres_mod._engine is not None:
            await postgres_mod._engine.dispose()
        postgres_mod._engine = None
        postgres_mod._session_factory = None

    try:
        asyncio.run(_check())
    except Exception:
        pytest.skip("Postgres unavailable — skip Drive/DB e2e")
    else:
        asyncio.run(_dispose())

