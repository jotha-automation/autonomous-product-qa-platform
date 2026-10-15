# APTP — Agent Guide

Autonomous Product Testing Platform: end-to-end product QA and Playwright automation with a multi-agent pipeline.

## Repositories

| Repo | Path | Role |
|------|------|------|
| **Backend** | `autonomous-product-qa-platform/` | FastAPI API, orchestrator, LLM agents, intake, reporting |
| **Frontend** | `../APTP-front/` | React 19 + Vite + Tailwind operator UI |
| **Demo target** | `../bank-qa-portal/` | Sample app under test (bank portal demo) |

## Stack

- **API:** FastAPI 3.x, async SQLAlchemy + asyncpg, Redis, pgvector
- **AI:** Azure OpenAI via `HybridAzureLLMClient` (reasoning vs codegen routing; enable with `APTP_HYBRID_LLM_ENABLED`)
- **Pipeline:** `ProductQAOrchestrator` in `src/orchestrator/engine.py`, stages in `src/orchestrator/stages.py`
- **Agents:** `BaseAgent` subclasses in `src/agents/`, shared memory via `EnhancedSharedMemory`
- **Integrations:** Google Drive intake, Azure Service Bus events, Playwright execution
- **Frontend:** React 19, Vite, TanStack Query, Radix UI, Tailwind 4

## Commands

```bash
# Backend (Docker)
make env && make up
make health

# Backend tests (no live Azure/Playwright)
APTP_ALLOW_MOCKS=true APTP_USE_MOCK_LLM=true APTP_MOCK_PLAYWRIGHT_EXECUTION=true APTP_SKIP_PLAYWRIGHT_INSTALL=true pytest tests/ -q

# Frontend (sibling repo)
cd ../APTP-front && npm install && npm run dev
```

## Specialized Cursor subagents

Delegate to these project subagents in `.cursor/agents/`:

| Subagent | File | Use when |
|----------|------|----------|
| AI Optimizer | `aptp-ai-optimizer.md` | Prompt/token tuning, LLM routing, cost/latency, agent output quality |
| Backend (FastAPI) | `aptp-backend-fastapi.md` | API routes, orchestrator, services, Postgres, tests |
| Frontend (React) | `aptp-frontend-react.md` | APTP-front UI, reports, pipeline UX, API client |
| Agentic Architect | `aptp-agentic-architect.md` | Multi-agent design, memory bus, pipeline stages, orchestration |
| AI Solutions Architect | `aptp-ai-solutions-architect.md` | Azure OpenAI, Drive, Service Bus, MCP, external AI platforms |
| Playwright Automation | `aptp-playwright-automation.md` | Playwright codegen, POM, spec generation, execution, evidence, self-healing, E2E reliability |
| Senior QA Engineer | `aptp-senior-qa-engineer.md` | Test strategy, obligations, manual vs automated, test design, traceability, deliverables, QA quality |

Invoke explicitly: *"Use the aptp-playwright-automation subagent to…"* or *"Use the aptp-senior-qa-engineer subagent to…"* — or let Agent delegate automatically from the subagent description.

## Conventions

- Minimize scope; match existing patterns in `src/agents/` and `src/api/`
- Stage names must match `PipelineStage` enum in `src/orchestrator/stages.py`
- Agent class names must match `llm_routing.py` profiles when adding new agents
- Tests use mocks: `APTP_ALLOW_MOCKS=true`, `APTP_USE_MOCK_LLM=true`
- Diagrams: `docs/diagrams/README.md`

## Security

- Never commit `.env` or API keys
- Run secrets through `src/services/run_secret_store.py` and `src/agents/guardrails/`
- Redact credentials in prompts via `agent_guardrails`
