import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import drive, intake, projects, runs, webhooks
from src.connectors.google_drive.connector import drive_status
from src.connectors.google_drive.watcher import start_drive_watcher, stop_drive_watcher
from src.memory.enhanced_memory import get_memory


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from src.runtime_policy import validate_startup_configuration

        app.state.runtime_policy = await validate_startup_configuration()
        from src.memory.enhanced_memory import get_memory
        from src.runtime_policy import ensure_redis_available

        ensure_redis_available(get_memory())
    except Exception as exc:
        app.state.runtime_policy = {"ok": False, "error": str(exc)}
        raise

    if os.getenv("APTP_INIT_DB", "false").lower() in {"1", "true", "yes"}:
        try:
            from src.services.postgres import init_db
            from src.knowledge.db import init_db as init_knowledge_db

            await init_db()
            await init_knowledge_db()
        except Exception:
            pass
    if os.getenv("APTP_ALLOW_MOCKS", "false").lower() not in {"1", "true", "yes"}:
        try:
            from src.connectors.google_drive.connector import get_google_drive_connector

            connector = get_google_drive_connector()
            if connector.is_configured:
                await connector.warmup()
                app.state.drive = "ready"
        except Exception as exc:
            logger.warning("Drive pre-warm skipped: %s", exc)
    start_drive_watcher()
    yield
    await stop_drive_watcher()


app = FastAPI(title="Autonomous Product Testing Platform", version="3.0.0", lifespan=lifespan)

_cors_origins = os.getenv("APTP_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(intake.router, prefix="/api/v1")
app.include_router(drive.router, prefix="/api/v1")


@app.get("/health")
async def health():
    from src.runtime_policy import data_provenance, use_mock_llm

    if use_mock_llm():
        from src.services.mock_llm import MockAzureFoundryClient

        llm_status = await MockAzureFoundryClient().health_check()
    else:
        try:
            from src.services.hybrid_llm import get_hybrid_llm_client

            llm_status = await get_hybrid_llm_client().health_check()
        except Exception as e:
            llm_status = {"status": "error", "error": str(e)}
    llm_status["real_only"] = not use_mock_llm()
    if not use_mock_llm():
        try:
            from src.services.hybrid_llm import get_hybrid_llm_client
            from src.services.llm_routing import routing_summary

            llm_status["routing"] = routing_summary()
            llm_status["usage_by_deployment"] = get_hybrid_llm_client().get_usage_by_deployment()
        except Exception:
            pass

    from src.services.mcp_client import mcp_intake_available
    from src.services.playwright_runtime import playwright_health_info

    mem = get_memory()
    playwright_status = playwright_health_info()
    redis_health = mem.redis_health()

    knowledge_status: dict = {"pgvector": "unknown"}
    try:
        from sqlalchemy import func, select
        from src.knowledge.db import get_session
        from src.knowledge.embeddings import embedding_dimensions
        from src.knowledge.models import KnowledgeChunk

        async with get_session() as session:
            result = await session.execute(select(func.count()).select_from(KnowledgeChunk))
            knowledge_status = {
                "pgvector": "ok",
                "chunks_indexed": result.scalar() or 0,
                "embedding_dimensions": embedding_dimensions(),
                "embedding_deployment": os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
            }
    except Exception as exc:
        knowledge_status = {"pgvector": "error", "detail": str(exc)[:200]}

    service_status = "ok"
    if llm_status.get("status") not in (None, "ok"):
        service_status = llm_status.get("status", "degraded")
    if playwright_status.get("live_enabled") and playwright_status.get("status") not in ("ok", "mock"):
        service_status = "degraded"
    if redis_health.get("status") == "required_missing":
        service_status = "degraded"

    return {
        "status": service_status,
        "service": "aptp",
        "data_provenance": data_provenance(),
        "llm": llm_status,
        "drive": drive_status(),
        "memory": mem.get_stats(),
        "redis": redis_health,
        "playwright": playwright_status,
        "knowledge": knowledge_status,
        "mcp_intake": {
            "available": mcp_intake_available(),
            "tools": [
                "list_intake_files",
                "list_qa_obligations",
                "build_scenario_outline",
                "search_intake_text",
            ],
        },
    }
