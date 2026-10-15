# APTP — Technical Diagrams

Mermaid diagrams for the Autonomous Product QA Platform. View in GitHub, VS Code (Mermaid preview), or [mermaid.live](https://mermaid.live).

## Index

| Diagram | File | Description |
|---------|------|-------------|
| System context | [system-context.mmd](./system-context.mmd) | Repos, external systems, and operator flow |
| Architecture | [architecture.mmd](./architecture.mmd) | Backend components, data stores, integrations |
| Full pipeline | [pipeline-full.mmd](./pipeline-full.mmd) | All `PipelineStage` values from intake to completion |
| Product QA (Phase 1) | [pipeline-product-qa.mmd](./pipeline-product-qa.mmd) | Planning agents: readiness → executive reporting |
| Automation (Phases 2–5) | [pipeline-automation.mmd](./pipeline-automation.mmd) | Playwright, execution, RCA, healing, evidence |
| Intake flow | [intake-flow.mmd](./intake-flow.mmd) | Drive sync → parse → catalog → normalization |
| Intake paths | [intake-paths.mmd](./intake-paths.mmd) | Portfolio folder vs PDF-first / TestLink |
| File catalog | [file-catalog.mmd](./file-catalog.mmd) | PRIMARY_SPEC, ASSET_BUNDLE, linking PDF + XLSX |
| Target URL gate | [target-url-gate.mmd](./target-url-gate.mmd) | Pause / resume when AUT URL is missing |
| Agent dependencies | [agent-dependencies.mmd](./agent-dependencies.mmd) | Agent call graph and memory artifacts |
| Memory bus | [memory-bus.mmd](./memory-bus.mmd) | RunMemoryBus, Redis scopes, pgvector |
| Playwright & evidence | [playwright-evidence.mmd](./playwright-evidence.mmd) | Codegen, execution, screenshots, PDF report |
| API routes | [api-routes.mmd](./api-routes.mmd) | FastAPI routers and main endpoints |
| Data stores | [data-stores.mmd](./data-stores.mmd) | Redis tiers, artifacts, secrets, Postgres |
| Bank portal demo | [bank-portal-demo.mmd](./bank-portal-demo.mmd) | INC1353166 end-to-end demo path |
| Deployment | [deployment.mmd](./deployment.mmd) | Local Docker vs Railway topology |
| Run sequence | [run-sequence.mmd](./run-sequence.mmd) | End-to-end operator + SSE timeline |

## Conventions

- **Solid arrows**: synchronous data flow or invocation order
- **Dashed arrows**: optional, async, or operator-triggered paths
- **Stage names** match `src/orchestrator/stages.py` (`PipelineStage` enum)
- **Agent names** match classes under `src/agents/`

## Related docs

- [LOCAL_DEV.md](../LOCAL_DEV.md) — local stack
- [DEMO_BANK_PORTAL.md](../DEMO_BANK_PORTAL.md) — bank-qa-portal demo
- [SETUP_GUIDE_POC.md](../SETUP_GUIDE_POC.md) — production POC
