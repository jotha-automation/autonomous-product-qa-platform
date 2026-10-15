# Autonomous Product Testing Platform (APTP)

End-to-end product QA and Playwright automation platform.

## Quick start — local full stack

```bash
# Backend (Docker) — requires filled .env
cd autonomous-product-qa-platform
cp .env.example .env   # fill all values
make env && make up

# Frontend (separate terminal)
cd ../APTP-front
cp .env.example .env && npm install && npm run dev
```

| Service | URL |
|---------|-----|
| API | http://localhost:8000/health |
| Swagger | http://localhost:8000/docs |
| Frontend | http://localhost:5173 |

**Guía completa local:** [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md)  
**Deploy Railway:** [docs/RAILWAY.md](docs/RAILWAY.md)  
**Operación producción:** [docs/SETUP_GUIDE_POC.md](docs/SETUP_GUIDE_POC.md)

## Docker commands

```bash
make env       # cp .env.example → .env
make up        # production-like API in Docker
make up-dev    # hot reload (docker-compose.dev.yml)
make down
make health
make logs
```

## Architecture

- **Phase 1:** Readiness → Requirements → Coverage → Test Design → Traceability → Risk → Knowledge → Reporting
- **Phase 2-5:** Playwright Architect → Generator → Execution → RCA → Self-Healing

See [docs/diagrams/README.md](docs/diagrams/README.md) for the full diagram index (architecture, pipeline, intake, API, evidence, demo).

## Environment

Production variables: [`.env.example`](.env.example)  
Local setup: [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md)  
Railway: [docs/RAILWAY.md](docs/RAILWAY.md) (Dashboard → Variables only).

## Running tests

Unit and integration tests use mock LLM and Playwright (no Azure or live browser required):

```bash
cd autonomous-product-qa-platform
source .venv/bin/activate   # or: python -m venv .venv && pip install -r requirements.txt
pip install -r requirements.txt

APTP_ALLOW_MOCKS=true \
APTP_USE_MOCK_LLM=true \
APTP_MOCK_PLAYWRIGHT_EXECUTION=true \
APTP_SKIP_PLAYWRIGHT_INSTALL=true \
pytest tests/ -q
```

E2E API tests (HTTP + mock AI). Drive multipart flows need Postgres; those tests skip automatically if the database is unavailable:

```bash
APTP_ALLOW_MOCKS=true \
APTP_USE_MOCK_LLM=true \
APTP_MOCK_PLAYWRIGHT_EXECUTION=true \
APTP_SKIP_PLAYWRIGHT_INSTALL=true \
APTP_API_KEY=test-e2e-api-key \
GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID=mock-aptp \
APTP_DISABLE_INTAKE_DB=true \
pytest tests/e2e/ -v
```

With Docker (Postgres + API), run the full suite including Drive → pipeline E2E:

```bash
make up
docker compose exec api pytest tests/ -q
```

Or locally against a running Postgres instance, omit `APTP_DISABLE_INTAKE_DB` and set `DATABASE_URL` as in `.env.example`.

Frontend contract tests live in the APTP-front repo: `npm run test` (see [APTP-front README](../APTP-front/README.md)).
