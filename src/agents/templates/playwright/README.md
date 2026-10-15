# Playwright boilerplate templates

Platform-owned files copied to every generated project. Domain content (pages, specs, auth helpers, upload files) is produced by the LLM at runtime.

## Files in this directory

| File | Generated as |
|------|----------------|
| `package.json.j2` | `package.json` |
| `playwright.config.ts.j2` | `playwright.config.ts` |
| `fixtures/dynamic-auth.ts` | `fixtures/dynamic-auth.ts` |
| `fixtures/evidence.ts` | `fixtures/evidence.ts` |
| `fixtures/video-evidence-reporter.ts` | `fixtures/video-evidence-reporter.ts` |

`.env` is merged at scaffold time (not stored here). Empty dirs created: `pages/`, `tests/`, `fixtures/`, `reports/`, `evidence/`.

See `docs/PLAYWRIGHT_BOILERPLATE.md` for ownership rules.
