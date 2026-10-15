# Deploy en Railway

El backend se despliega como **Docker image** (`Dockerfile` + `railway.toml`).  
**Todas las variables se configuran en Railway Dashboard → Variables**, no en el repositorio.

Referencia completa: [`.env.example`](../.env.example)

## 1. Crear proyecto Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Selecciona `autonomous-product-qa-platform`
3. Railway detecta `railway.toml` y construye con `Dockerfile`

## 2. Añadir plugins

| Plugin | Variable que expone |
|--------|---------------------|
| **PostgreSQL** | `DATABASE_URL` |
| **Redis** | `REDIS_URL` |

### Convertir DATABASE_URL a asyncpg

Railway Postgres entrega `postgresql://...`. APTP necesita el driver async:

```text
postgresql+asyncpg://USER:PASS@HOST:PORT/railway
```

Sustituye solo el prefijo `postgresql://` por `postgresql+asyncpg://`.

## 3. Variables (Dashboard → Variables)

Copia la estructura de `.env.example` y completa cada valor:

```bash
# Security
APTP_API_KEY=<secreto-largo-aleatorio>

# Infrastructure (desde plugins Railway)
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://...

# Azure OpenAI (Foundry)
APTP_USE_MOCK_LLM=false
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://<proyecto>.services.ai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini

# Google Drive — única configuración de carpeta: el ID del system root APTP/
GOOGLE_DRIVE_CREDENTIALS_JSON={"type":"service_account",...}
GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID=<ID_carpeta_APTP>
GOOGLE_DRIVE_WEBHOOK_SECRET=<secreto>

# Frontend
APTP_CORS_ORIGINS=https://tu-front.vercel.app

# Bootstrap (primera vez)
APTP_INIT_DB=true
APTP_RUN_MIGRATIONS=true

# Drive watcher
APTP_DRIVE_WATCH_ENABLED=true
```

`PORT` la inyecta Railway automáticamente — **no la definas**.

### Google Drive

1. Crea o identifica la carpeta raíz **APTP/** en Drive.
2. Copia su **Folder ID** → `GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID`.
3. Comparte esa carpeta con el email del service account con rol **Editor**.
4. Pega el JSON completo del service account en `GOOGLE_DRIVE_CREDENTIALS_JSON`.

No hace falta configurar nombre de carpeta ni IDs legacy adicionales; el backend asume `APTP/` como system root.

## 4. Networking

1. Servicio API → **Settings** → **Generate Domain**
2. Health check: `GET https://<tu-dominio>.up.railway.app/health`
3. Swagger: `https://<tu-dominio>.up.railway.app/docs`

### Playwright (E2E en contenedor)

La imagen Docker incluye navegadores preinstalados (`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`, `APTP_SKIP_PLAYWRIGHT_INSTALL=true`). La versión está fijada en `src/playwright_version.py` y debe coincidir con el tag de `mcr.microsoft.com/playwright`.

**Tras actualizar Playwright en el repo**, fuerza un rebuild en Railway (Redeploy → Clear build cache) o localmente:

```bash
make down && make up --build
```

Verifica en `/health`:

```json
"playwright": {
  "status": "ok",
  "version": "1.61.1",
  "browsers_preinstalled": true,
  "live_enabled": true
}
```

Si `playwright.status` es `degraded` o ves `chromium_headless_shell-* executable missing`, la imagen desplegada no coincide con la versión npm del proyecto generado — reconstruye la imagen API.

## 5. Frontend apuntando a Railway

En `APTP-front/.env`:

```bash
VITE_APTP_API_URL=https://<tu-dominio>.up.railway.app
VITE_APTP_API_KEY=<mismo APTP_API_KEY de Railway>
```

Incluye la URL del front en `APTP_CORS_ORIGINS` del backend.

## 6. Checklist post-deploy

- [ ] `/health` → `status: ok`, `llm.status: ok`, `llm.mode` ≠ mock
- [ ] `playwright.status: ok`, `playwright.version` coincide con `src/playwright_version.py`
- [ ] `drive.mode: real`, `system_root_folder_id` = tu ID de APTP/
- [ ] Service account con **Editor** en la carpeta APTP/
- [ ] Front → Settings → API health OK
- [ ] Subir proyecto bajo APTP/ → pipeline + reportes

## 7. Logs y migraciones

- Logs: Railway → servicio API → **Deployments** → View Logs
- Migraciones: `entrypoint.sh` ejecuta `alembic upgrade head` en cada deploy
- Tras el primer deploy exitoso: `APTP_INIT_DB=false`
