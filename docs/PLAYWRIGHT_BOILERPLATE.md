# Playwright boilerplate ownership (APTP)

## Platform owns (minimal scaffold only)

Copied on every run from `src/agents/templates/playwright/`:

| File | Purpose |
|------|---------|
| `package.json` | Playwright + TypeScript devDependencies |
| `playwright.config.ts` | Loads `.env`, `baseURL`, reporters |
| `.env` | Skeleton; **values filled by LLM** from intake PDF + credentials xlsx |
| `fixtures/dynamic-auth.ts` | Resolves `APTP_SECRET_*` at runtime |
| `fixtures/evidence.ts` | Step screenshots → `evidence/{test}/{step}.png` |
| `fixtures/video-evidence-reporter.ts` | Full-run video → `evidence/{test}/run.webm` |

Empty directories: `pages/`, `tests/`, `fixtures/`, `reports/`, `evidence/`.

The platform **does not** ship domain page objects, flow helpers, test-data, or upload fixtures.

## Runtime LLM agent owns (all test content)

After live **explorer** session (target URL + login + selector inventory):

1. **`pages/*.ts`** — POMs using discovered `data-testid` / roles
2. **`fixtures/*.ts`** — auth helpers, test-data, flow helpers (except `dynamic-auth`, `evidence`)
3. **`fixtures/archivos/*`** — pipe-delimited upload files per PDF/xlsx
4. **`tests/**/*.spec.ts`** — one EMPRESARIAL / obligation per test
5. **`.env` values** — `BASE_URL` + `APTP_SECRET_*` from credentials spreadsheet

Inputs: test plan, explorer `generated-inventory.json`, interpreted intake, asset bundle.

## Generator flow

```
Explorer (Playwright) → inventory + verified steps
        ↓
LLM codegen (reasoning) → pages, fixtures, specs, env
        ↓
Scaffold merge → write files → playwright test --list → playwright test
```

## Protected files (never overwritten by LLM output)

- `fixtures/dynamic-auth.ts`
- `fixtures/evidence.ts`
- `package.json`, `playwright.config.ts` (scaffold)

See `docs/prompts/APTP-CURSOR-PLAYWRIGHT-BOILERPLATE-PROMPT.md` for the full checklist.
