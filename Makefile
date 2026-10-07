# Common tasks. `make help` lists them.
.PHONY: help install db migrate topics api web test lint fmt typecheck check run collect classify score seed

help:  ## show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## install Python + web dependencies
	uv sync
	cd web && npm ci

db:  ## start local Postgres (pgvector) in Docker
	docker compose up -d db

migrate:  ## apply migrations
	uv run alembic upgrade head

topics:  ## load topics/*.yaml into the DB
	uv run python -m app.jobs.sync_topics

api:  ## FastAPI on :8000 with reload
	uv run uvicorn app.api.main:app --reload --port 8000

web:  ## Next.js dev server on :3000
	cd web && npm run dev

test:  ## run the test suite (DB tests need TEST_DATABASE_URL)
	uv run pytest

lint:  ## ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## auto-format
	uv run ruff check --fix .
	uv run ruff format .

typecheck:  ## mypy + tsc
	uv run mypy app
	cd web && npx tsc --noEmit

check: lint typecheck test  ## everything CI runs

run:  ## full daily pipeline (collect -> classify -> embed -> score)
	uv run python -m app.jobs.run_all

collect:  ## collectors only
	uv run python -m app.jobs.collect

classify:  ## classification only
	uv run python -m app.jobs.classify

score:  ## scoring only
	uv run python -m app.jobs.score

seed:  ## fill the LOCAL db with synthetic demo data for UI work
	uv run python -m app.jobs.seed_demo
