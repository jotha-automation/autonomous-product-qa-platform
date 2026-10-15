# Local development — backend (Docker) + frontend (Vite)

Guía para probar **todo el sistema en tu máquina** con la misma configuración real que producción.

Variables de referencia: [`.env.example`](../.env.example)

## Arquitectura local

```text
┌─────────────────────┐     proxy /api      ┌──────────────────────────────┐
│  APTP-front :5173   │ ──────────────────► │  aptp-api :8000 (Docker)     │
│  npm run dev        │                     │  + Postgres + Redis (Docker) │
└─────────────────────┘                     │  Google Drive + Azure LLM    │
                                            └──────────────────────────────┘
```

## 1. Configurar `.env`

```bash
cd autonomous-product-qa-platform
cp .env.example .env
```

Completa **todas** las variables en `.env`:

| Variable | Notas locales |
|----------|---------------|
| `APTP_API_KEY` | Secreto que usarás también en el front |
| `APTP_ALLOW_MOCKS` | Debe ser `false` — sin mocks en producción/local real |
| `APTP_USE_MOCK_LLM` | `false` — solo respuestas Azure |
| `APTP_SKIP_PLAYWRIGHT_INSTALL` | `false` — instalar navegadores por corrida; `true` cuando ya están pre-instalados |
| `APTP_MOCK_PLAYWRIGHT_EXECUTION` | `false` — E2E Playwright real en Docker; `true` para ejecución simulada (mocks) |
| `AZURE_OPENAI_*` | Foundry: `DEPLOYMENT` (codegen, p. ej. Kimi-K2.6), `DEPLOYMENT_REASONING` (p. ej. gpt-4.1), `APTP_HYBRID_LLM_ENABLED=true` |
| `AZURE_OPENAI_ENDPOINT` | Mismo **Project endpoint** para chat y embeddings (`https://<proyecto>.services.ai.azure.com`) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-small` + `APTP_EMBEDDING_DIMENSIONS=1536`; tras cambiar dims: `alembic upgrade head` (migración 006) y re-procesar proyectos |
| `GOOGLE_DRIVE_CREDENTIALS_JSON` | JSON inline del service account |
| `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID` | ID de la carpeta **APTP/** en Drive |
| `GOOGLE_DRIVE_WEBHOOK_SECRET` | Secreto para webhooks Drive |
| `APTP_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` |

Docker Compose **sobrescribe** solo la conectividad interna:

- `DATABASE_URL` → Postgres del contenedor
- `REDIS_URL` → Redis del contenedor

El resto de variables se leen desde tu `.env`. Compose **no** incluye fallbacks mock: `make up` falla si `.env` está vacío o incompleto.

```bash
make env    # valida .env (crea plantilla desde .env.example si falta)
make up     # solo arranca si la validación pasa
```

## 2. Backend (Docker)

```bash
make up          # stack estable
# o
make up-dev      # hot reload del código Python
```

Verificar:

```bash
make health
# http://localhost:8000/docs
```

| Servicio | Puerto |
|----------|--------|
| API | 8000 |
| Postgres | 5432 (user/pass/db: `aptp`) |
| Redis | 6379 |

```bash
make down      # detener
make logs      # logs API
make test      # pytest en contenedor
```

## 3. Frontend (sin Docker)

```bash
cd ../APTP-front
cp .env.example .env
npm install
npm run dev
```

Abre http://localhost:5173

| Variable | Valor local |
|----------|-------------|
| `VITE_APTP_API_URL` | *(vacío)* — proxy Vite → `:8000` |
| `VITE_APTP_API_KEY` | Mismo valor que `APTP_API_KEY` en backend |

En **Settings** del front confirma API health (`status: ok`, `drive.mode: real`).

## 4. Prueba end-to-end

1. En Google Drive, crea un subfolder bajo **APTP/** (p. ej. `APTP-MiProyecto-R1/`) con BRD/PRD/UC/AC.
2. Front → **Sync** o espera el watcher → el proyecto aparece en la lista.
3. Lanza QA desde el front o vía API.
4. Sigue el progreso SSE y revisa reportes al terminar.
5. Al completar, los entregables se suben **sin comprimir** a `{proyecto}/QA Platform Results/{run_id}/` en Drive.

> Si en logs aparece `storageQuotaExceeded` / *Service Accounts do not have storage quota*, la carpeta **APTP/** está en **My Drive personal**. Mueve **APTP/** a un **Shared Drive** (Workspace) con la SA como Content manager, o configura `GOOGLE_DRIVE_IMPERSONATE_USER`. Ver [SETUP_GUIDE_POC.md §6.6](./SETUP_GUIDE_POC.md). El front muestra esos resultados y permite re-procesar hasta **3 ejecuciones** por proyecto (1 inicial + 2 re-procesos).

Migración de contadores de procesamiento (Postgres):

```bash
# Desde la raíz del repo (usa alembic.ini en la raíz — no existe deployment/migrations/alembic.ini)
pip install 'psycopg[binary]>=3.1.18'
export DATABASE_URL=postgresql+psycopg://aptp:aptp@localhost:5432/aptp
alembic upgrade head

# O con Docker (Postgres ya levantado con make up):
make migrate
```

Alembic usa el driver sync `psycopg` (no `psycopg2`); la app sigue usando `asyncpg`.

### Agentes QA (LLM + memoria)

- Roles enterprise en `src/agents/prompts/enterprise.py` (system prompt + presupuesto de tokens por agente).
- Bus de memoria por run: `src/agents/memory_bus.py` (`run:{id}:test_plan`, `execution:{id}:rca`, etc.).
- **pgvector**: tras requirements y al final del pipeline se indexan chunks en `knowledge_chunks`; los agentes de automation leen `knowledge_context` vía búsqueda semántica.
- Los mocks solo están permitidos en **pytest** (`APTP_ALLOW_MOCKS=true` en `tests/conftest.py`), no en `make up`.
- Producción: `APTP_USE_MOCK_LLM=false` y modelo Azure con JSON mode para máxima calidad.

### Playwright en vivo (Docker)

La imagen API usa `mcr.microsoft.com/playwright:v1.61.1-jammy` (ver `src/playwright_version.py`) + Python. `docker-compose.yml` fuerza:

```yaml
APTP_MOCK_PLAYWRIGHT_EXECUTION: "false"
APTP_SKIP_PLAYWRIGHT_INSTALL: "true"
PLAYWRIGHT_BROWSERS_PATH: /ms-playwright
APTP_PLAYWRIGHT_VERSION: "1.61.1"
```

Los `package.json` generados fijan `@playwright/test` a la misma versión exacta (sin `^`) para evitar que `npm install` descargue navegadores incompatibles con la imagen.

**Tras cambiar la versión de Playwright**, reconstruye la imagen:

```bash
make down && make up --build
```

Tras `make up-dev`, verifica:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# playwright.status: ok, playwright.version: 1.61.1, playwright.browsers_preinstalled: true
```

Migración pgvector:

```bash
alembic upgrade head   # incluye 005_knowledge_chunks
```

Si Postgres ya tenía tablas creadas por `APTP_INIT_DB` (sin historial Alembic), marca la revisión actual y aplica solo lo pendiente:

```bash
alembic stamp 003
alembic upgrade head
```

Si ves `relation "pipeline_run_records" already exists` al arrancar: la tabla existe pero Alembic quedó en `007`. Con `make up-dev`, la migración `008` es idempotente; si persiste:

```bash
docker compose exec api alembic stamp 008
docker compose exec api alembic upgrade head
```

En `.env` local con Docker, usa `APTP_INIT_DB=false` y deja `APTP_RUN_MIGRATIONS=true`.

### Enterprise intake (autonomous)

Intake routing is **automatic** — no `APTP_INTAKE_MODE` or MCP toggles. The pipeline detects portfolio structure (BRD/PRD/UC/AC) vs unstructured PDF/Excel and adapts; mixed folders merge both paths.

- **Guardrails**: blocked file types, empty projects, and encrypted PDFs are rejected before interpretation (`INTAKE_GUARDRAILS` stage).
- **Secrets**: credentials resolved at runtime go to `RunSecretStore` (`run:{id}` scope) — never in JSON reports or UI.
- **Deliverable**: E59 Excel template at `src/intake/templates/deliverables/set-de-pruebas-template.xlsx`; download via `GET /api/v1/runs/{runId}/artifacts/deliverable.xlsx`.
- **MCP client**: `src/services/mcp_client.py` (in-process when `mcp/aptp_intake_tools/` is present).

### Demo banco (INC1353166 + bank-qa-portal)

Ver [DEMO_BANK_PORTAL.md](./DEMO_BANK_PORTAL.md) — fixture `tests/fixtures/mock-drive/APTP/APTP-Bank-Demo-R1`, portal mock en repo `bank-qa-portal`, y flujo APTP con `target_url` pausable.

API directa (sustituye `YOUR_API_KEY`):

```bash
curl -s http://localhost:8000/health \
  -H "X-Api-Key: YOUR_API_KEY" | python3 -m json.tool

curl -X POST http://localhost:8000/api/v1/runs/async \
  -H "X-Api-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"TU-PROYECTO","mode":"phase1"}'
```

## 5. Producción en Railway

Ver [RAILWAY.md](./RAILWAY.md) — mismas variables que `.env.example`, configuradas en el dashboard.
