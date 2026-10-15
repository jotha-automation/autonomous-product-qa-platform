.PHONY: help env up up-dev down logs health test migrate front

help:
	@echo "APTP local commands:"
	@echo "  make env      Validate .env (create from .env.example if missing)"
	@echo "  make up       Start stack (requires filled .env)"
	@echo "  make up-dev   Start stack with hot reload"
	@echo "  make down     Stop stack"
	@echo "  make logs     Tail API logs"
	@echo "  make health   Curl /health"
	@echo "  make test     Run pytest in container"
	@echo "  make migrate  Run Alembic migrations (Postgres must be up)"
	@echo "  make front    Print frontend start instructions"

env:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example — fill ALL values, then run make up"; \
		exit 1; \
	fi
	@sh deployment/docker/validate-env.sh .env

up: env
	docker compose up --build -d
	@echo ""
	@echo "API:  http://localhost:8000/health"
	@echo "Docs: http://localhost:8000/docs"
	@echo "Front: cd ../APTP-front && npm run dev → http://localhost:5173"

up-dev: env
	chmod +x deployment/docker/entrypoint.sh deployment/docker/validate-env.sh
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

down:
	docker compose down

logs:
	docker compose logs -f api

health:
	@curl -sf http://localhost:8000/health | python3 -m json.tool || (echo "API not ready" && exit 1)

test:
	docker compose exec api pytest tests/ -q --tb=short

migrate:
	docker compose exec api alembic upgrade head

front:
	@echo "cd ../APTP-front && cp .env.example .env && npm install && npm run dev"
