#!/bin/sh
set -e

PORT="${PORT:-8000}"
HOST="${APTP_HOST:-0.0.0.0}"

echo "[aptp] Starting Autonomous Product Testing Platform"
echo "[aptp] PORT=${PORT} APTP_INIT_DB=${APTP_INIT_DB:-false}"

if [ "${APTP_WAIT_FOR_DB:-true}" = "true" ]; then
  python - <<'PY'
import os, sys, time
import asyncio

async def wait_postgres():
    url = os.getenv("DATABASE_URL", "")
    if not url or "postgres" not in url:
        return
    try:
        import asyncpg
    except ImportError:
        return
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    for attempt in range(30):
        try:
            conn = await asyncpg.connect(dsn)
            await conn.close()
            print("[aptp] Postgres is ready")
            return
        except Exception as exc:
            print(f"[aptp] Waiting for Postgres ({attempt + 1}/30): {exc}")
            await asyncio.sleep(2)
    print("[aptp] Postgres not ready after retries", file=sys.stderr)
    sys.exit(1)

asyncio.run(wait_postgres())
PY
fi

if [ "${APTP_RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[aptp] Running Alembic migrations..."
  alembic upgrade head || echo "[aptp] Alembic warning (continuing)"
fi

if [ "${APTP_UVICORN_RELOAD:-false}" = "true" ]; then
  echo "[aptp] Dev mode: uvicorn --reload"
  exec uvicorn src.main:app --host "$HOST" --port "$PORT" --reload --reload-dir /app/src
fi

WORKERS="${APTP_WEB_WORKERS:-1}"
if [ "$WORKERS" -gt 1 ] 2>/dev/null; then
  echo "[aptp] Production mode: ${WORKERS} workers"
  exec uvicorn src.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
fi

echo "[aptp] Starting uvicorn"
exec uvicorn src.main:app --host "$HOST" --port "$PORT"
