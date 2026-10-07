# Topic Influence Mapper — conventions for Claude Code

Internal ColdSpark tool: per topic, collect public posts (X, Bluesky, Reddit, YouTube, news,
podcasts), LLM-classify them as on/off topic, build a who-cites-whom graph, and rank authors.
`PLAN.md` is the full brief and the record of decisions; read the section you're touching
before changing behaviour, and edit PLAN.md (with the reason in the commit) when reality
disagrees with it.

## Layout

- `app/` — Python 3.12 backend (collectors, pipeline, jobs, FastAPI). SQLAlchemy **Core** +
  psycopg 3; no ORM sessions. Tables are in `app/models.py`; DDL lives in Alembic migrations.
- `app/collectors/` — one module per platform, all subclass `Collector` in `base.py`.
  `collect()` yields normalized `Post` objects; `base.py` does upserts, `collector_runs`,
  retries, dry-run.
- `app/pipeline/` — `classify.py`, `embed.py`, `graph.py`, `score.py`. Pure logic is kept in
  functions that take plain data so it can be unit-tested without a DB.
- `app/jobs/` — CLI entrypoints: `sync_topics`, `collect`, `classify`, `embed`, `score`,
  `run_all`. Run as `python -m app.jobs.<name>`.
- `app/api/` — FastAPI. Every route except `/health` requires `X-Internal-Key`.
- `web/` — Next.js (app router, Tailwind, shadcn-style components, Clerk). The browser never
  calls FastAPI; server components use `web/lib/api.ts` which adds the key.
- `topics/*.yaml` — topic definitions (`example.yaml` is documentation and is skipped).

## Commands

```bash
make help                                 # common tasks (wraps the commands below)
uv sync                                   # install (uv.lock pins everything)
docker compose up -d db                   # local Postgres 16 + pgvector (+ influence_test db)
cp .env.example .env
uv run alembic upgrade head               # migrations
uv run python -m app.jobs.sync_topics     # load topics/*.yaml
uv run python -m app.jobs.collect --topic pa-energy --platform bluesky --dry-run
uv run python -m app.jobs.run_all         # collect -> classify -> embed -> score
uv run python -m app.jobs.seed_demo       # synthetic demo data (local DB only)
uv run uvicorn app.api.main:app --reload  # API on :8000
uv run pytest                             # tests (DB tests need TEST_DATABASE_URL)
uv run ruff check . && uv run ruff format --check . && uv run mypy app
cd web && npm install && npm run dev      # UI on :3000
```

New migration: `uv run alembic revision -m "what"` then write SQL by hand in
`alembic/versions/` (keep `app/models.py` in sync). Never edit an applied migration.

## Rules

- Never commit `.env` or any key. Secrets live in Railway / Vercel env settings.
- Type hints everywhere; `ruff` for lint+format; `mypy` non-strict.
- Tests never call third-party APIs live. Record real responses into `tests/fixtures/` and
  test normalization against them. DB tests are marked `@pytest.mark.db` and use
  `TEST_DATABASE_URL` (the suite drops and recreates the schema there — never point it at
  a real database).
- Collectors never swallow exceptions: the error goes to `collector_runs.error` and the job
  moves on to the next platform/topic. A failing collector must not block the others.
- Collectors are idempotent (`ON CONFLICT DO NOTHING` on `(platform, platform_id)`); re-runs
  refresh engagement counts but insert no new rows.
- X reads are budgeted (`app/collectors/x_budget.py`, `x_budget` table). Never call the X API
  outside that allocator.
- All LLM/embedding calls go through `app/llm.py` (OpenAI-compatible, `base_url`/`model` from
  env), which logs to `llm_calls` and enforces `LLM_DAILY_TOKEN_CAP`. Never hardcode a
  provider.
- Score snapshots are history: never delete `author_scores` rows.
- Commits: small, per phase, message says which PLAN.md phase / acceptance criterion it
  advances.

## Deploy

- **DB:** Supabase (pgvector enabled). Use the *pooled* connection string (port 6543) as
  `DATABASE_URL`; `app/db.py` detects it and disables prepared statements.
- **API:** Railway service using `railway.toml` (runs migrations + topic sync pre-deploy).
- **Daily pipeline:** second Railway service from this repo using `railway.cron.toml`
  (`python -m app.jobs.run_all`, cron `0 10 * * *` UTC).
- **Web:** Vercel project with root directory `web/`; env `API_URL`, `INTERNAL_API_KEY`, Clerk
  keys, `ALLOWED_EMAIL_DOMAINS`. Full steps in README.md "Deploy".

## Gotchas

- `app/models.py` must mirror the migrations by hand; tests run the real migrations.
- X retweet objects carry the original tweet's metrics — the normalizer zeroes them.
- `post_sources` records which topic's retrieval found a post; classification only runs on
  those (post, topic) pairs, so a post is never classified against unrelated topics.
- Fixtures in `tests/fixtures/` were hand-built from API docs; replace with `--record`
  output when credentials exist (keep the filenames).
- Web: auth is skipped only when `NODE_ENV=development` and no Clerk keys are set; in
  production a missing Clerk config returns 503. Pages and server actions call
  `requireAllowedUser()`; keep doing that for any new page or action.
