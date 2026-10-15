# Guía operativa — Autonomous Product Testing Platform (APTP)

**Versión:** 2.0 — Producción autónoma  
**Audiencia:** Operación real con LLM (Azure AI Foundry), Google Drive personal y despliegue en nube  
**Repositorio:** `autonomous-product-qa-platform/`  
**Primer proyecto de validación:** `demo/portfolio-webapp/` → https://jsh.up.railway.app/

---

## Tabla de contenidos

1. [Qué vas a lograr](#1-qué-vas-a-lograr)
2. [Arquitectura productiva (sin depender de tu Mac)](#2-arquitectura-productiva-sin-depender-de-tu-mac)
3. [Prerrequisitos y cuentas](#3-prerrequisitos-y-cuentas)
4. [Fase A — Desplegar APTP en la nube (Railway)](#4-fase-a--desplegar-aptp-en-la-nube-railway)
5. [Fase B — Azure AI Foundry (LLM real)](#5-fase-b--azure-ai-foundry-llm-real)
6. [Fase C — Google Drive (intake documental)](#6-fase-c--google-drive-intake-documental)
7. [Fase D — Primer proyecto real: portfolio-webapp (paso a paso)](#7-fase-d--primer-proyecto-real-portfolio-webapp-paso-a-paso)
8. [Operación: cargar un nuevo proyecto de QA](#8-operación-cargar-un-nuevo-proyecto-de-qa)
9. [Formas de disparar el pipeline](#9-formas-de-disparar-el-pipeline)
10. [Variables de entorno (referencia)](#10-variables-de-entorno-referencia)
11. [Verificación end-to-end](#11-verificación-end-to-end)
12. [Desarrollo local (opcional)](#12-desarrollo-local-opcional)
13. [Troubleshooting](#13-troubleshooting)
14. [Costos estimados](#14-costos-estimados)
15. [Checklist Go-Live productivo](#15-checklist-go-live-productivo)

---

## 1. Qué vas a lograr

Al terminar esta guía tendrás APTP funcionando **de forma autónoma en la nube**:

```text
Google Drive (documentos del cliente)
  → Intake automático (API / webhook / watcher)
  → 12 agentes con LLM real (Azure AI Foundry)
  → Diseño de pruebas + Playwright (POM)
  → Ejecución + RCA + Self-healing
  → Reportes ejecutivos
```

**Nada depende de tu máquina local** una vez desplegado: Postgres, Redis, API y credenciales viven en Railway (o tu PaaS). Tu Mac solo se usa para configurar cuentas, subir documentos a Drive y validar resultados.

**GitHub** es motor de CI/CD y almacenamiento de código; **no** dispara el pipeline de QA. El trigger es Google Drive, el webhook o la API `/runs`.

---

## 2. Arquitectura productiva (sin depender de tu Mac)

```mermaid
flowchart TB
  subgraph gdrive [Google Drive - tu cuenta personal]
    Folder[Carpeta APTP-CLIENTE-R1]
    Docs[BRD / PRD / UC / AC]
    Folder --> Docs
  end

  subgraph railway [Railway - APTP desplegado]
    API[APTP API FastAPI]
    PG[(Postgres + pgvector)]
    Redis[(Redis)]
    PW[Playwright runner profile]
  end

  subgraph azure [Azure AI Foundry]
    LLM[gpt-4o-mini / Kimi / etc.]
  end

  subgraph target [App bajo prueba]
    Site[jsh.up.railway.app]
  end

  Docs -->|Service Account lectura| API
  API --> PG
  API --> Redis
  API --> LLM
  API -->|E2E generado| Site
  PW --> Site
```

| Componente | Dónde corre | Obligatorio |
|------------|-------------|-------------|
| API APTP | Railway (Docker) | Sí |
| Postgres + pgvector | Railway plugin o contenedor | Sí |
| Redis | Railway plugin o contenedor | Sí |
| LLM | Azure AI Foundry | Sí (producción) |
| Documentos intake | Google Drive personal | Sí (producción) |
| Playwright E2E | Perfil `playwright-runner` o CI | Opcional fase 1 |
| Service Bus | Azure | No (usa InMemoryBus) |

---

## 3. Prerrequisitos y cuentas

| Cuenta / herramienta | Para qué | Costo típico |
|---------------------|----------|--------------|
| **Railway** | Host API + Postgres + Redis | Hobby ~$5/mes |
| **Azure** (cuenta personal) | AI Foundry / OpenAI deployments | Créditos trial + pay-as-you-go |
| **Google Cloud** (proyecto ligado a tu Gmail) | Drive API + Service Account | Gratis en uso personal |
| **Google Drive personal** | Carpeta por proyecto/cliente | Gratis |
| **GitHub** | Repo + CI | Gratis |

Herramientas locales **solo para setup inicial** (no para operación diaria):

```bash
git --version
docker --version   # opcional, para probar imagen antes de deploy
curl --version
```

---

## 4. Fase A — Desplegar APTP en la nube (Railway)

### 4.1 Crear proyecto Railway

1. Entra a https://railway.app/ e inicia sesión con GitHub.
2. **New Project** → **Deploy from GitHub repo** → selecciona `autonomous-product-qa-platform`.
3. Railway detectará el `Dockerfile` en la raíz.

### 4.2 Añadir Postgres y Redis

En el mismo proyecto Railway:

1. **+ New** → **Database** → **PostgreSQL** (anota `DATABASE_URL` interna).
2. **+ New** → **Database** → **Redis** (anota `REDIS_URL`).

> Railway expone URLs internas del tipo `postgresql://...@postgres.railway.internal:5432/railway`. APTP necesita driver async: convierte a `postgresql+asyncpg://...` (mismo host, usuario, password, DB).

### 4.3 Variables de entorno en Railway (servicio API)

En el servicio API → **Variables**, configura al menos:

```bash
APTP_API_KEY=<genera-un-secreto-largo-aleatorio>
APTP_USE_MOCK_LLM=false
APTP_INIT_DB=true
APTP_SKIP_PLAYWRIGHT_INSTALL=true
APTP_MOCK_PLAYWRIGHT_EXECUTION=false

DATABASE_URL=postgresql+asyncpg://<user>:<pass>@postgres.railway.internal:5432/railway
REDIS_URL=redis://default:<pass>@redis.railway.internal:6379

PLAYWRIGHT_PROJECTS_ROOT=generated-test-projects
MAX_HEAL_ATTEMPTS=2
HEAL_CONFIDENCE_THRESHOLD=0.6
```

Las variables de Azure y Google Drive se añaden en las fases B y C.

### 4.4 Dominio público

1. Servicio API → **Settings** → **Networking** → **Generate Domain**.
2. Anota la URL pública, por ejemplo: `https://aptp-production.up.railway.app`
3. Verifica salud:

```bash
curl -s https://aptp-production.up.railway.app/health | jq
```

Respuesta esperada (parcial, antes de configurar LLM/Drive):

```json
{
  "status": "ok",
  "service": "aptp",
  "llm": { "status": "error", ... },
  "drive": { "configured": false, "mode": "mock" }
}
```

### 4.5 Alternativa: Docker Compose en un VPS

Si prefieres un VPS (DigitalOcean, Hetzner, etc.):

```bash
git clone <tu-repo> && cd autonomous-product-qa-platform
cp .env.example .env
# Edita .env con credenciales reales
docker compose up -d --build
```

Expón el puerto 8000 con un reverse proxy (Caddy/Nginx) y certificado TLS. El resto de la guía es idéntico; sustituye la URL de Railway por tu dominio.

---

## 5. Fase B — Azure AI Foundry (LLM real)

### 5.1 Crear proyecto en Azure AI Foundry

1. Portal Azure → https://portal.azure.com (cuenta Microsoft personal).
2. Busca **Azure AI Foundry** → crea un **proyecto** (ej. `aptp-prod`).
3. Dentro del proyecto → **Models + endpoints** → **Deploy model**.
4. Modelo recomendado para costo/calidad en validación: **`gpt-4o-mini`**.
5. Anota estos tres valores:

| Valor | Dónde encontrarlo | Ejemplo |
|-------|-------------------|---------|
| **Endpoint** | Project settings → Keys and endpoints | `https://aptp-prod.services.ai.azure.com` |
| **Deployment name** | Nombre exacto del deploy | `gpt-4o-mini` |
| **API Key** | Keys and endpoints | `(secreto)` |

> **Importante:** Los modelos Foundry (incl. Kimi) usan endpoint `*.services.ai.azure.com`, **no** `*.openai.azure.com`. El nombre del deployment debe coincidir **exactamente** con el portal.

### 5.2 Configurar variables en Railway

```bash
AZURE_OPENAI_API_KEY=<tu-key>
AZURE_OPENAI_ENDPOINT=https://<proyecto>.services.ai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_MAX_TOKENS=4096
AZURE_OPENAI_TEMPERATURE=0.2

APTP_USE_MOCK_LLM=false
```

Redeploy o reinicia el servicio API.

### 5.3 Verificar LLM

```bash
curl -s https://aptp-production.up.railway.app/health | jq '.llm'
```

Esperado:

```json
{
  "status": "ok",
  "api_mode": "foundry-v1",
  "endpoint_kind": "foundry-project",
  "deployment": "gpt-4o-mini",
  "sample": "OK"
}
```

### 5.4 Control de costos

- Configura **Budget alert** en Azure → Cost Management → Budgets (ej. $15/mes).
- Un run `phase1` con portfolio demo ≈ 8–12 llamadas LLM.
- Un run `full` ≈ 15–25 llamadas.

---

## 6. Fase C — Google Drive (intake documental)

APTP lee documentos desde carpetas de **tu Google Drive personal** mediante una **Service Account** de Google Cloud. Tú subes los archivos; APTP los descarga y procesa sin intervención manual.

### 6.1 Crear proyecto Google Cloud

1. https://console.cloud.google.com/ → inicia sesión con tu Gmail.
2. **New Project** → nombre: `aptp-drive-prod`.
3. No necesitas facturación para Drive API en uso personal bajo cuotas free.

### 6.2 Habilitar Google Drive API

1. **APIs & Services** → **Library**.
2. Busca **Google Drive API** → **Enable**.

### 6.3 Crear Service Account

1. **APIs & Services** → **Credentials** → **Create Credentials** → **Service Account**.
2. Nombre: `aptp-drive-reader`.
3. Rol: ninguno especial (solo lectura vía carpeta compartida).
4. **Keys** → **Add Key** → **JSON** → descarga el archivo (ej. `aptp-drive-sa.json`).

**Nunca subas este JSON al repositorio.**

Anota el email de la service account:

```text
aptp-drive-reader@aptp-drive-prod.iam.gserviceaccount.com
```

### 6.4 Configurar credenciales en Railway

Opción recomendada — JSON inline (Railway no tiene filesystem persistente):

1. Abre `aptp-drive-sa.json` y copia **todo el contenido** en una sola línea.
2. En Railway → Variables:

```bash
GOOGLE_DRIVE_CREDENTIALS_JSON={"type":"service_account","project_id":"...",...}
GOOGLE_DRIVE_WEBHOOK_SECRET=$(openssl rand -hex 32)
```

`GOOGLE_DRIVE_WEBHOOK_SECRET` **no viene de Google**: lo generas tú (mismo valor al registrar el canal push y en Railway). Ver [§9 — Push Notifications](#google-drive-push-notifications--registrar-canal-watch).

### 6.5 Modelo de carpeta única APTP (recomendado)

La plataforma usa **una sola carpeta raíz en Drive** configurada en Railway:

```bash
GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID=<ID_DE_LA_CARPETA_APTP>
```

**Tú solo creas subcarpetas bajo `APTP`** — cada subcarpeta directa = un cliente/proyecto. APTP las descubre, indexa y procesa automáticamente.

```text
Google Drive/
└── APTP/                              ← ÚNICA carpeta configurada en APTP (Folder ID en .env)
    ├── APTP-JSH-Portfolio-R1/         ← proyecto 1 (subcarpeta = proyecto)
    │   ├── manifest.json              ← project_id, target_url, etc.
    │   ├── 01-Business-Requirements/ …
    │   └── intake/ …
    ├── APTP-ClienteX-R2/               ← proyecto 2
    └── APTP-ClienteY-MVP/             ← proyecto 3 (nueva → evento project_folder_created)
```

**Operación para nuevos clientes:**

1. Crear subcarpeta bajo `APTP/` (ej. `APTP-ACME-CRM-2026`).
2. Subir documentación con subcarpetas (`01-Business-Requirements/`, etc.).
3. (Recomendado) Copiar/adaptar `manifest.json` con `project_id` y `target_url`.
4. El watcher o `POST /api/v1/intake/sync-system` detecta la carpeta nueva y los cambios.

APTP hace **auto-discovery recursivo** dentro de cada proyecto: clasifica BRD/PRD/UC/AC por scaffolding (igual que `demo/portfolio-webapp/`).

**Mock local:** estructura en `demo/APTP/APTP-JSH-Portfolio-R1/` (copia de portfolio-webapp).

Los **4 tipos obligatorios** para readiness (`BRD`, `PRD`, `UC`, `AC`) se detectan automáticamente por patrones como `01-Business-Requirements`, `use-cases`, `intake/brd.txt`, etc.

| Carpeta / patrón | Tipo inferido |
|------------------|---------------|
| `01-Business-Requirements`, `*brd*` | BRD |
| `02-Product-Requirements`, `*prd*` | PRD |
| `04-Use-Cases`, `*use*case*` | UC |
| `05-Acceptance-Criteria`, `*acceptance*`, `intake/ac.txt` | AC |

### 6.6 Compartir carpeta APTP/ con la Service Account

**Este paso es obligatorio.** Sin permisos correctos obtendrás error 403.

#### ⚠️ Cuota de almacenamiento (error frecuente tras dar Editor)

Las **service accounts no tienen cuota** en *My Drive* personal. Aunque compartas la carpeta como Editor, **crear carpetas puede funcionar pero subir archivos falla** con:

```text
Service Accounts do not have storage quota … use shared drives or OAuth delegation
```

**Soluciones válidas para subir desde el front/API:**

| Opción | Requisito | Configuración |
|--------|-----------|---------------|
| **A — Shared Drive (recomendado)** | Google Workspace | Mover **APTP/** a un Shared Drive; SA = Content manager del Shared Drive |
| **B — Impersonation** | Google Workspace + admin | Domain-wide delegation + `GOOGLE_DRIVE_IMPERSONATE_USER=tu@empresa.com` |
| **C — Solo lectura** | Cualquier Drive | Subes carpetas tú en Drive; APTP solo indexa y procesa (sin upload UI) |

> **Gmail personal (sin Workspace):** la opción A y B no aplican. Usa **C** (subir manualmente) o migra APTP/ a una cuenta Workspace.

#### A — Shared Drive (paso a paso)

1. Google Drive → **Shared drives** → **New** (ej. `APTP-Production`).
2. Crea o mueve la carpeta **APTP/** dentro de ese Shared Drive.
3. **Manage members** del Shared Drive → añade el email de la service account.
4. Rol: **Content manager** (o Manager).
5. Copia el **Folder ID** de `APTP/` → `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID`.
6. Redeploy API.

#### B — Domain-wide delegation (paso a paso)

1. Google Cloud Console → Service account → **Advanced settings** → enable **Domain-wide delegation**.
2. Google Admin Console → Security → API controls → Domain-wide delegation → add Client ID de la SA.
3. Scope: `https://www.googleapis.com/auth/drive`
4. Railway / `.env`:

```bash
GOOGLE_DRIVE_IMPERSONATE_USER=tu-usuario@tu-dominio.com
```

5. La carpeta **APTP/** puede estar en *My Drive* de ese usuario; compártela con la SA como Editor (opcional si impersonas al dueño).
6. Redeploy API.

#### Compartir carpeta (cuando no usas Shared Drive)

1. Clic derecho en **APTP/** → **Share**.
2. Añade el email `aptp-drive-reader@....iam.gserviceaccount.com`.
3. Rol: **Editor**.
4. Desmarca "Notify people" → **Share**.

Solo lectura/sync funciona con Editor en My Drive; **upload desde APTP requiere A o B arriba**.

### 6.7 Obtener el Folder ID

Abre la carpeta en el navegador. La URL tiene esta forma:

```text
https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz
                                      └─────────────────────────────┘
                                              FOLDER_ID
```

Copia ese ID; lo usarás en variables y payloads.

### 6.8 Verificar Drive en health

Tras configurar credenciales y redeploy:

```bash
curl -s https://aptp-production.up.railway.app/health | jq '.drive'
```

Esperado:

```json
{
  "configured": true,
  "mode": "real",
  "registry_enabled": true,
  "watch_enabled": false
}
```

### 6.9 Registro en Postgres (índice + changelog)

APTP usa **PostgreSQL** (ya desplegado con la API) para:

| Tabla | Propósito |
|-------|-----------|
| `intake_root_folders` | Carpetas raíz registradas por `project_id` |
| `intake_subfolders` | Subcarpetas descubiertas (`01-Business-Requirements`, etc.) |
| `intake_file_records` | Snapshot por archivo (fingerprint, tipo inferido, estado) |
| `intake_change_logs` | Auditoría: `folder_created`, `file_created`, `file_modified`, `file_deleted`, `file_processed` |

**Migración:**

```bash
export DATABASE_URL=postgresql+asyncpg://aptp:aptp@localhost:5432/aptp
alembic upgrade head
```

O en primer deploy: `APTP_INIT_DB=true` (crea todas las tablas vía SQLAlchemy).

**API de consulta:**

```bash
# Carpetas raíz indexadas
curl -s "https://<API_URL>/api/v1/intake/registry?project_id=JSH-PORTFOLIO-2026" \
  -H "X-Api-Key: <API_KEY>" | jq

# Changelog reciente
curl -s "https://<API_URL>/api/v1/intake/changes?project_id=JSH-PORTFOLIO-2026&limit=50" \
  -H "X-Api-Key: <API_KEY>" | jq

# Preview discovery sin ejecutar pipeline
curl -X POST "https://<API_URL>/api/v1/intake/discover" \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"JSH-PORTFOLIO-2026","drive_folder_id":"<FOLDER_ID>"}'
```

El **watcher** (`APTP_DRIVE_WATCH_ENABLED=true`) compara fingerprints en cada poll y solo reprocesa archivos nuevos o modificados.

---

## 7. Fase D — Primer proyecto real: portfolio-webapp (paso a paso)

Este es el **walkthrough completo** con los archivos en `demo/portfolio-webapp/`. Sigue los pasos en orden.

### Resumen del demo

| Campo | Valor |
|-------|-------|
| **project_id** | `JSH-PORTFOLIO-2026` |
| **App bajo prueba** | https://jsh.up.railway.app/ |
| **Documentos intake** | `demo/portfolio-webapp/intake/*.txt` |
| **Documentación completa** | `demo/portfolio-webapp/01-Business-Requirements/` … `05-Acceptance-Criteria/` |

### Paso D.1 — Crear estructura APTP en Google Drive

1. Crea la carpeta raíz **`APTP`** en tu Drive personal.
2. Comparte **`APTP`** con la service account (**Editor**).
3. Copia el **Folder ID de APTP** → `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID` en Railway.
4. Dentro de `APTP`, crea la subcarpeta **`APTP-JSH-Portfolio-R1`**.
5. Sube el contenido de **`demo/APTP/APTP-JSH-Portfolio-R1/`** (o `demo/portfolio-webapp/`) con todas las subcarpetas.

Mínimo viable (solo `intake/`):

```text
demo/portfolio-webapp/intake/brd.txt
demo/portfolio-webapp/intake/prd.txt
demo/portfolio-webapp/intake/uc.txt
demo/portfolio-webapp/intake/ac.txt
```

Recomendado (scaffolding completo, auto-discovery):

```text
demo/portfolio-webapp/   → subir tal cual a Drive
```

APTP recorrerá todas las subcarpetas, inferirá tipos y registrará cambios en Postgres.

### Paso D.2 — Compartir y copiar Folder ID

1. Comparte la carpeta con la service account (**Editor**).
2. Copia el **Folder ID** de la URL.

Ejemplo: si tu ID es `1XyZ_example_folder_id`, guárdalo.

### Paso D.3 — Variables Railway (carpeta sistema APTP)

```bash
GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID=<ID_CARPETA_APTP>
APTP_DRIVE_DEFAULT_MODE=full
APTP_DRIVE_WATCH_ENABLED=true
APTP_DRIVE_WATCH_TRIGGER_RUN=true
APTP_DRIVE_POLL_INTERVAL=300
GOOGLE_DRIVE_WEBHOOK_SECRET=<openssl rand -hex 32>
```

Tras deploy, registra el canal push: [§9 — registrar canal (watch)](#google-drive-push-notifications--registrar-canal-watch).

### Paso D.4 — Sincronizar todo APTP (primera prueba)

Indexa todos los proyectos bajo `APTP/` sin ejecutar pipeline:

```bash
curl -X POST https://<API_URL>/api/v1/intake/sync-system \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"trigger_run": false}'
```

Listar proyectos detectados:

```bash
curl -s https://<API_URL>/api/v1/intake/projects -H "X-Api-Key: <API_KEY>" | jq
```

### Paso D.5 — Disparar pipeline para portfolio

Solo el proyecto `APTP-JSH-Portfolio-R1`:

```bash
curl -X POST https://<API_URL>/api/v1/runs \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "JSH-PORTFOLIO-2026",
    "use_system_root": true,
    "project_folder_name": "APTP-JSH-Portfolio-R1",
    "mode": "phase1"
  }'
```

O sincronizar y ejecutar todos los proyectos con cambios:

```bash
curl -X POST https://<API_URL>/api/v1/intake/sync-system \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"trigger_run": true, "mode": "phase1"}'
```

Payload pre-armado:

```bash
curl -X POST https://<API_URL>/api/v1/webhooks/google-drive \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d @demo/portfolio-webapp/sample-drive-run-payload.json
```

**Guarda el `run_id` de la respuesta.**

### Paso D.5 — Validar resultados Fase 1

```bash
RUN_ID=<run_id>

curl -s "https://<API_URL>/api/v1/runs/${RUN_ID}" \
  -H "X-Api-Key: <API_KEY>" | jq '.status, .stage'

curl -s "https://<API_URL>/api/v1/runs/${RUN_ID}/reports" \
  -H "X-Api-Key: <API_KEY>" | jq 'keys'
```

| Campo | Esperado |
|-------|----------|
| `status` | `completed` |
| `results.readiness.status` | `READY` |
| `results.requirements` | Objeto con requirements extraídos |
| `results.traceability` | Matriz generada |
| `results.reports` | QA, Product, Executive |

### Paso D.6 — Pipeline completo (Fase 1 + Playwright + RCA + Healing)

```bash
curl -X POST https://<API_URL>/api/v1/runs \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "JSH-PORTFOLIO-2026",
    "mode": "full",
    "drive_folder_id": "<FOLDER_ID>",
    "metadata": {
      "target_url": "https://jsh.up.railway.app/"
    }
  }'
```

Con Playwright live en Railway, configura además:

```bash
APTP_MOCK_PLAYWRIGHT_EXECUTION=false
APTP_SKIP_PLAYWRIGHT_INSTALL=true
```

y activa el perfil `playwright-runner` si usas Docker Compose; en Railway puede requerir un servicio separado con imagen Playwright.

### Paso D.7 — Disparar vía webhook (simula trigger automático)

Equivalente al trigger de Drive sin polling:

```bash
curl -X POST https://<API_URL>/api/v1/webhooks/google-drive \
  -H "X-Api-Key: <API_KEY>" \
  -H "X-Webhook-Secret: <GOOGLE_DRIVE_WEBHOOK_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "JSH-PORTFOLIO-2026",
    "drive_folder_id": "<FOLDER_ID>",
    "mode": "full",
    "trigger_run": true,
    "metadata": {
      "target_url": "https://jsh.up.railway.app/"
    }
  }'
```

### Paso D.8 — Validación manual del sitio bajo prueba

Cruza los reportes generados con el plan en `demo/portfolio-webapp/12-QA-Test-Plan/QA-Test-Plan-R1.md`:

1. Smoke: hero, nav, contact form, footer.
2. Experience vs. `demo/portfolio-webapp/10-Test-Data/experience-baseline.csv`.
3. Gap conocido: ítem **Projects** en nav sin sección visible (documentado en PRD).

---

## 8. Operación: cargar un nuevo proyecto de QA

Cuando llegue un **nuevo cliente o release**, repite este procedimiento operativo (no requiere redeploy si solo cambias carpeta/payload):

### Checklist operativo — nuevo proyecto

| # | Acción | Responsable | Dónde |
|---|--------|-------------|-------|
| 1 | Definir `project_id` único (ej. `ACME-CRM-2026-R2`) | QA Lead | Convención interna |
| 2 | Crear carpeta `APTP/APTP-<Cliente>-<Release>/` | QA / PO | Google Drive |
| 3 | Subir BRD, PRD, UC, AC (mínimo 4 archivos) | PO / BA | Carpeta Drive |
| 4 | (Opcional) Subir user stories, NFR, test data | QA | Misma carpeta |
| 5 | Compartir carpeta **APTP/** con service account (**Editor**) | QA Lead | Drive → Share |
| 6 | Copiar Folder ID | QA Lead | URL del navegador |
| 7 | Registrar proyecto en APTP | QA Lead | API `/projects` o payload |
| 8 | Disparar run | QA Lead / automático | API o webhook |
| 9 | Revisar reportes | QA Lead | GET `/runs/{id}/reports` |
| 10 | Archivar resultados | QA | Exportar JSON / logs Railway |

### Registrar proyecto (opcional pero recomendado)

```bash
curl -X POST https://<API_URL>/api/v1/projects \
  -H "X-Api-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ACME CRM Release 2",
    "drive_folder_id": "<NUEVO_FOLDER_ID>"
  }'
```

### Convención de nombres de archivo

APTP infiere el tipo de documento por nombre y contenido. Usa nombres claros:

```text
BRD-ACME-CRM-v1.0.pdf
PRD-ACME-CRM-R2.md
use-cases.md          → detectado como UC
acceptance-criteria.md → detectado como AC
```

Los archivos en `intake/*.txt` del demo son plantilla de nombres mínimos.

---

## 9. Formas de disparar el pipeline

| Método | Cuándo usarlo | Endpoint / acción |
|--------|---------------|-------------------|
| **API manual** | Primera prueba, control total | `POST /api/v1/runs` con `drive_folder_id` |
| **Webhook manual** | Integración, sync bajo demanda | `POST /api/v1/webhooks/google-drive` |
| **Push (watch channel)** | Cambios instantáneos en **APTP/** | Google → webhook (ver abajo) |
| **Watcher (polling)** | Autonomía sin renovar canal | `APTP_DRIVE_WATCH_ENABLED=true` |
| **document_paths** | Solo desarrollo local con archivos en disco | `POST /api/v1/runs` |

### Modo autónomo — Drive Watcher (polling)

Alternativa al push de Google: APTP consulta Drive cada N segundos sobre la carpeta **APTP/**:

```bash
APTP_DRIVE_WATCH_ENABLED=true
APTP_DRIVE_POLL_INTERVAL=300          # segundos (mín. 60)
GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID=<ID_carpeta_APTP>
APTP_DRIVE_DEFAULT_MODE=full
```

Flujo:

1. Subes o actualizas documentos bajo `APTP/<proyecto>/`.
2. En ≤5 min (según `APTP_DRIVE_POLL_INTERVAL`) APTP detecta cambios.
3. Descarga documentos y ejecuta el pipeline.
4. Consultas reportes con el `run_id` en logs de Railway.

> **Recomendado para latencia baja:** usar [Push Notifications (watch)](#google-drive-push-notifications--registrar-canal-watch) en lugar de polling.

### Google Drive Push Notifications — registrar canal (watch)

Notificaciones **instantáneas** cuando cambia algo bajo la carpeta raíz **APTP/** (sin esperar el polling del watcher).

Documentación Google: [Push notifications](https://developers.google.com/drive/api/guides/push)

#### Requisitos previos

| Requisito | Detalle |
|-----------|---------|
| API desplegada con HTTPS | URL pública Railway, ej. `https://aptp-production.up.railway.app` |
| Variables en Railway | `GOOGLE_DRIVE_CREDENTIALS_JSON`, `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID`, `GOOGLE_DRIVE_WEBHOOK_SECRET` |
| Carpeta compartida | Service account con **Editor** (o al menos acceso de lectura) sobre la carpeta APTP/ |
| Secreto webhook | Generado por ti: `openssl rand -hex 32` → `GOOGLE_DRIVE_WEBHOOK_SECRET` |

**Endpoint que recibirá Google:**

```text
https://<API_URL>/api/v1/webhooks/google-drive
```

Google **no** envía `X-Api-Key`. APTP valida el canal con el header `X-Goog-Channel-Token` (= tu `GOOGLE_DRIVE_WEBHOOK_SECRET`).

#### Paso 1 — Generar el secreto del canal

```bash
openssl rand -hex 32
```

Copia el valor en Railway → `GOOGLE_DRIVE_WEBHOOK_SECRET` y redeploy.

#### Paso 2 — Registrar el canal con el script incluido

Desde tu máquina (con Python 3.11+ y dependencias del repo):

```bash
cd autonomous-product-qa-platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_DRIVE_CREDENTIALS_JSON='{"type":"service_account",...}'   # JSON completo
export GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID='<ID_carpeta_APTP>'
export GOOGLE_DRIVE_WEBHOOK_SECRET='<mismo valor que Railway>'
export APTP_WEBHOOK_URL='https://<API_URL>/api/v1/webhooks/google-drive'

python scripts/register_drive_watch.py
```

Respuesta esperada (ejemplo):

```json
{
  "kind": "api#channel",
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "resourceId": "abc123XYZ",
  "resourceUri": "https://www.googleapis.com/drive/v3/files/<FOLDER_ID>",
  "expiration": "1735689600000"
}
```

**Guarda** `id` (channel id) y `resourceId` — los necesitas para renovar o detener el canal.

Google enviará de inmediato un POST de verificación (`X-Goog-Resource-State: sync`). APTP responde `200` con `{"status":"ok","message":"watch channel active"}`.

#### Paso 2 (alternativa) — Registrar con curl

Obtén un access token del service account (ej. con [gcloud](https://cloud.google.com/sdk)):

```bash
gcloud auth activate-service-account --key-file=aptp-drive-sa.json
ACCESS_TOKEN=$(gcloud auth print-access-token)
CHANNEL_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
EXPIRATION_MS=$(($(date +%s) * 1000 + 604800000))   # ~7 días (máximo Drive)

curl -s -X POST \
  "https://www.googleapis.com/drive/v3/files/${GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID}/watch?supportsAllDrives=true" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${CHANNEL_ID}\",
    \"type\": \"web_hook\",
    \"address\": \"https://<API_URL>/api/v1/webhooks/google-drive\",
    \"token\": \"${GOOGLE_DRIVE_WEBHOOK_SECRET}\",
    \"expiration\": ${EXPIRATION_MS}
  }"
```

#### Paso 3 — Qué hace APTP al recibir un cambio

1. Google POST → `/api/v1/webhooks/google-drive` (cuerpo vacío, headers `X-Goog-*`).
2. APTP valida `X-Goog-Channel-Token` contra `GOOGLE_DRIVE_WEBHOOK_SECRET`.
3. Ejecuta `sync_system_root` sobre `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID` (todas las subcarpetas/proyectos bajo **APTP/**).
4. Si hay archivos nuevos o modificados, dispara el pipeline QA (por defecto `trigger_run: true`).

#### Paso 4 — Probar manualmente (sin esperar cambios en Drive)

Simula una llamada de integración con API key **o** webhook secret:

```bash
curl -X POST "https://<API_URL>/api/v1/webhooks/google-drive" \
  -H "X-Api-Key: <APTP_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"use_system_root": true, "trigger_run": false}'
```

Con solo webhook secret:

```bash
curl -X POST "https://<API_URL>/api/v1/webhooks/google-drive" \
  -H "X-Webhook-Secret: <GOOGLE_DRIVE_WEBHOOK_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"use_system_root": true, "trigger_run": false}'
```

#### Paso 5 — Renovar el canal (obligatorio)

Los canales de Drive **expiran** (máximo ~7 días). Antes de la fecha en `expiration`:

1. Vuelve a ejecutar `python scripts/register_drive_watch.py` (genera un canal nuevo), **o**
2. Programa un cron semanal con las mismas variables de entorno.

Opcional — detener un canal antiguo:

```bash
curl -s -X POST "https://www.googleapis.com/drive/v3/channels/stop" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"id\": \"<CHANNEL_ID>\", \"resourceId\": \"<RESOURCE_ID>\"}"
```

#### Watcher vs Push

| Método | Latencia | Configuración | Renovación |
|--------|----------|---------------|------------|
| `APTP_DRIVE_WATCH_ENABLED=true` | ~5 min (polling) | Solo env vars | Ninguna |
| Push (watch channel) | Segundos | Script + HTTPS público | Cada ~7 días |

Puedes usar **solo uno** o ambos (push para cambios rápidos + watcher como respaldo).

---

## 10. Variables de entorno (referencia)

### Producción (Railway) — mínimo

| Variable | Obligatoria | Descripción |
|----------|-------------|-------------|
| `APTP_API_KEY` | Sí | Secreto para header `X-Api-Key` |
| `APTP_USE_MOCK_LLM` | Sí | `false` en producción |
| `AZURE_OPENAI_API_KEY` | Sí | Key de Foundry |
| `AZURE_OPENAI_ENDPOINT` | Sí | `https://<proj>.services.ai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | Sí | Nombre exacto del deployment |
| `GOOGLE_DRIVE_CREDENTIALS_JSON` | Sí | JSON service account (inline) |
| `DATABASE_URL` | Sí | `postgresql+asyncpg://...` |
| `REDIS_URL` | Sí | URL Redis |
| `APTP_INIT_DB` | Recomendada | `true` en primer deploy |
| `GOOGLE_DRIVE_WEBHOOK_SECRET` | Sí (push) | Secreto tuyo; token del canal watch |
| `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID` | Sí | ID único de la carpeta **APTP/** |
| `APTP_DRIVE_WATCH_ENABLED` | Opcional | `true` = polling autónomo (alternativa al push) |
| `APTP_DEFAULT_TARGET_URL` | Recomendada | URL app bajo prueba |
| `APTP_MOCK_PLAYWRIGHT_EXECUTION` | Opcional | `false` para E2E real; `true` para ejecución simulada |
| `APTP_SKIP_PLAYWRIGHT_INSTALL` | Opcional | `true` para omitir instalación de navegadores si ya están instalados |

Ver `.env.example` para la lista completa.

---

## 11. Verificación end-to-end

### 11.1 Tests unitarios (CI / pre-deploy)

```bash
cd autonomous-product-qa-platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=.
export APTP_USE_MOCK_LLM=true
export APTP_MOCK_PLAYWRIGHT_EXECUTION=true
export APTP_SKIP_PLAYWRIGHT_INSTALL=true
pytest tests/ -v
```

### 11.2 Health completo (producción)

```bash
curl -s https://<API_URL>/health | jq
```

Validar:

- `llm.status` = `"ok"`
- `drive.configured` = `true`
- `drive.mode` = `"real"`

### 11.3 Matriz de pruebas recomendada

| # | Prueba | Acción | Entorno |
|---|--------|--------|---------|
| 1 | Unit tests | `pytest tests/ -v` | Local / CI |
| 2 | Health LLM | `curl /health` → llm ok | Railway |
| 3 | Health Drive | `curl /health` → drive real | Railway |
| 4 | Readiness READY | Run phase1 portfolio desde Drive | Railway + Drive |
| 5 | Readiness NOT_READY | Carpeta solo con BRD+PRD | Railway + Drive |
| 6 | Full pipeline | Run full con Drive + LLM real | Railway |
| 7 | Push (watch) | Registrar canal → §9; subir archivo en Drive | Railway |
| 8 | Watcher | Subir archivo → auto-run (polling) | Railway |
| 9 | Reportes | GET `/runs/{id}/reports` | Railway |
| 10 | CI GitHub | Push → workflow green | GitHub |

---

## 12. Desarrollo local (opcional)

Para iterar código antes de deploy. **No es el entorno operativo.**

```bash
cd autonomous-product-qa-platform
cp .env.example .env
# Configura .env con credenciales reales o mock

docker compose up --build
```

Probar con archivos locales (sin Drive):

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H "X-Api-Key: dev-api-key" \
  -H "Content-Type: application/json" \
  -d @demo/portfolio-webapp/sample-run-payload.json
```

`sample-run-payload.json` usa `document_paths` apuntando a `demo/portfolio-webapp/intake/`.

---

## 13. Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|----------------|----------|
| `llm.status: error` | Azure mal configurado | Verifica endpoint `*.services.ai.azure.com`, deployment name, key |
| Azure 404 deployment | Nombre incorrecto | Debe coincidir exacto con Foundry portal |
| `drive.mode: mock` | Sin credenciales Google | Configura `GOOGLE_DRIVE_CREDENTIALS_JSON` |
| Google Drive 403 insufficientParentPermissions | SA sin Editor en APTP/ | Share → **Editor** |
| Google Drive 403 storageQuotaExceeded | SA sin cuota en My Drive personal | Shared Drive **o** `GOOGLE_DRIVE_IMPERSONATE_USER` |
| `NOT_READY` readiness | Faltan BRD/PRD/UC/AC | Sube los 4 tipos; revisa nombres de archivo |
| `No documents found` webhook | Folder ID vacío o sin permisos | Verifica ID y sharing |
| `401 Unauthorized` API | API key incorrecta | Header `X-Api-Key` |
| `401` webhook | Secret o token incorrecto | Manual: `X-Webhook-Secret` o `X-Api-Key`. Google push: `token` del canal = `GOOGLE_DRIVE_WEBHOOK_SECRET` |
| Push sin eventos | Canal expirado (~7 días) | Re-ejecutar `scripts/register_drive_watch.py` |
| Run muy lento | LLM real + full mode | Normal; usa `phase1` para iterar |
| Playwright no corre | Mock mode activo | `APTP_MOCK_PLAYWRIGHT_EXECUTION=false` |

### Logs en Railway

Servicio API → **Deployments** → **View Logs**. Busca:

- `Azure client ready` — LLM conectado
- `Google Drive connector initialized` — Drive real
- `Drive intake folder=...` — documentos descargados
- `Drive watcher started` — polling activo

---

## 14. Costos estimados

| Servicio | Costo mensual típico |
|----------|---------------------|
| Railway (API + PG + Redis) | $5–15 |
| Azure OpenAI (gpt-4o-mini, ~50 runs) | $1–10 |
| Google Drive API | $0 |
| GitHub Actions | $0 (free tier) |
| **Total estimado** | **$6–25/mes** |

---

## 15. Checklist Go-Live productivo

### Infraestructura nube
- [ ] API desplegada en Railway con dominio público HTTPS
- [ ] Postgres y Redis conectados (`DATABASE_URL`, `REDIS_URL`)
- [ ] `/health` retorna 200

### LLM real
- [ ] Azure Foundry: endpoint + deployment + key configurados
- [ ] `APTP_USE_MOCK_LLM=false`
- [ ] `/health` → `llm.status: ok`

### Google Drive
- [ ] Service account creada y JSON en Railway
- [ ] `/health` → `drive.mode: real`
- [ ] Carpeta portfolio compartida con service account

### Primer proyecto (portfolio-webapp)
- [ ] 4 archivos intake subidos a Drive
- [ ] Run `phase1` → `READY` + reportes
- [ ] Run `full` → suite Playwright generada
- [ ] Reportes revisados vs. QA Test Plan

### Seguridad
- [ ] `APTP_API_KEY` ≠ `dev-api-key`
- [ ] JSON service account fuera del repo
- [ ] `GOOGLE_DRIVE_WEBHOOK_SECRET` configurado
- [ ] Budget alert en Azure

### Autonomía
- [ ] (Opcional) `APTP_DRIVE_WATCH_ENABLED=true` probado
- [ ] Procedimiento operativo nuevo proyecto documentado internamente

---

## Referencias rápidas

| Recurso | Ubicación |
|---------|-----------|
| Demo cliente | `demo/portfolio-webapp/` |
| Payload API local | `demo/portfolio-webapp/sample-run-payload.json` |
| Payload Drive | `demo/portfolio-webapp/sample-drive-run-payload.json` |
| Manifest proyecto | `demo/portfolio-webapp/manifest.json` |
| Variables entorno | `.env.example` |
| Sitio bajo prueba | https://jsh.up.railway.app/ |
| Swagger (API desplegada) | `https://<API_URL>/docs` |
| Arquitectura | `docs/diagrams/architecture.mmd` |

---

*Guía APTP v3.0 — operación productiva autónoma con Azure AI Foundry y Google Drive personal.*
