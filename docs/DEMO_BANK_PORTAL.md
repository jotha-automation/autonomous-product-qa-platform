# Demo: Portal Bancario + APTP

> Diagramas: [docs/diagrams/bank-portal-demo.mmd](./diagrams/bank-portal-demo.mmd) · [file-catalog.mmd](./diagrams/file-catalog.mmd) · [target-url-gate.mmd](./diagrams/target-url-gate.mmd)

Guía para ejecutar una demo end-to-end con **bank-qa-portal** como aplicación bajo prueba y **autonomous-product-qa-platform** como orquestador de QA.

## 1. Desplegar bank-qa-portal

```bash
cd bank-qa-portal
npm install
npm run dev          # local http://localhost:8080
# o Railway: conectar repo, build npm run build, start npm run start
```

Variables Railway:
- `VITE_PUBLIC_APP_URL` = URL pública del servicio
- `PORT` (inyectado por Railway)

Verificar: `curl https://YOUR-APP.up.railway.app/health.json`

## 2. Configurar intake APTP

### Recomendado — solo el PDF en Google Drive

1. Crear carpeta del proyecto bajo la raíz APTP en Drive (ej. `APTP-Bank-Demo-R1`)
2. Subir **únicamente** el PDF TestLink (`pruebas-inc1353166.pdf` o equivalente INC1353166)
3. No hace falta estructura BRD/PRD/UC/AC ni `manifest.json` con documentos extra
4. Al ejecutar el pipeline, APTP:
   - interpreta el PDF y sintetiza BRD/PRD/UC/AC virtuales
   - extrae obligaciones por caso `EMPRESARIAL-xxxxx`
   - **pausa** si no encuentra `target_url` en el PDF → ingresar URL del portal en APTP-front

### Opcional — fixture local de referencia

```bash
cd autonomous-product-qa-platform
# tests/fixtures/mock-drive/APTP/APTP-Bank-Demo-R1/  (referencia, no obligatorio)
```

### URL del AUT

Cuando el gate pida URL, usar la pública de **bank-qa-portal** (Railway o `http://localhost:8080`).

## 3. Ejecutar pipeline

**API local:**

```bash
make up-dev
curl -X POST http://localhost:8000/api/v1/runs \
  -H "X-Api-Key: dev-api-key" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/mock-drive/APTP/APTP-Bank-Demo-R1/sample-run-payload.json
```

**Frontend APTP-front:**
1. Crear proyecto con intake del banco
2. Si no hay URL en documentos → el pipeline **pausa** y pide URL
3. Ingresar URL del portal Railway → reanuda automáticamente

## 4. Flujo manual de referencia (INC1353166)

| Paso | Módulo | Acción |
|------|--------|--------|
| 1 | Login | 312 / 20242587 / 123456 / token 63866019 |
| 2 | Inscripciones → Crear | Encabezado + Digitar o Archivo (BBVA) |
| 3 | Validar | Validar proceso |
| 4 | Actualizar | → Enviada a otro banco |
| 5 | Consultas | Buscar Procesos, ver detalle |
| 6 | (opcional) | Parámetro días hábiles → auto-rechazo |

## 5. Reiniciar estado demo

En el portal: botón **Reiniciar demo** (header) limpia inscripciones y pagos en localStorage.

## 6. Repos relacionados

| Repo | Rol |
|------|-----|
| `bank-qa-portal` | AUT mock Portal Empresarial |
| `autonomous-product-qa-platform` | Pipeline agentes + Playwright |
| `APTP-front` | UI operador |

### Archivos de soporte en la misma carpeta Drive

Además del PDF de pruebas, puedes subir hojas de cálculo u otros insumos. APTP clasifica cada archivo automáticamente:

| Archivo ejemplo | Rol | Cuándo se usa |
|-----------------|-----|----------------|
| `pruebas-inc1353166.pdf` | **PRIMARY_SPEC** | Extrae obligaciones, diseño de pruebas, plan Playwright |
| `test-credentials.xlsx` | **ASSET_BUNDLE** | Login (`Usuarios`), escenarios de transferencia (`Data`), destinos (`Destinos`) |

El catálogo de intake enlaza el Excel al PDF de la misma carpeta. Las credenciales del sheet `Usuarios` se cargan en `login_user.*` para Playwright (sin hardcodear en specs).

```
APTP-real-pdf-intake/
  pruebas-inc1353166.pdf    → spec
  test-credentials.xlsx     → credenciales + datos de ejecución
```

## 7. Automatización Playwright (Fase 2)

Para intake INC1353166 / bank portal, APTP genera specs con:

- Login vía `bank-portal-auth` + secretos demo (`login_user.*`)
- Locators `data-testid` reales del portal (`crear-digitar`, `validar-page`, etc.)
- `captureStepEvidence` por paso → PNG en `evidence/` → PDF de ejecución
- Obligaciones `manual` (días hábiles) excluidas de Playwright

Credenciales demo se registran automáticamente si el PDF no trae CSV de assets.

- EMPRESARIAL-13327/13326 — Error comunicación (días hábiles)
- EMPRESARIAL-13330/13331 — Crear digitar / archivo
- EMPRESARIAL-13333/13337 — Validar / Actualizar
- EMPRESARIAL-13350 — Consultas
- Escenario Pagado Parcial — Pagos con Válida Inscripción = No
