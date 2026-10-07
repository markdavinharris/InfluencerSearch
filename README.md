# Topic Influence Mapper

Internal ColdSpark tool. Give it a topic ("Pennsylvania energy policy", "Allegheny County
property reassessment") and it:

1. **collects** public discussion from X, Bluesky, Reddit, YouTube, news (GDELT + RSS) and
   podcasts;
2. **classifies** each post with an LLM: on topic or not, stance, one-line summary;
3. **builds a graph** of who quotes, reposts, replies to, mentions and links to whom;
4. **ranks** accounts by a composite of network centrality (PageRank), engagement and reach;
5. shows the ranking in a **web UI**: a ranked table per topic, author pages, a "rising" view
   and a status page.

It runs once a day. `PLAN.md` is the full design brief and decision log; `CLAUDE.md` holds
the working conventions.

```
topics/*.yaml ─► collectors ─► posts/authors ─► LLM classifier ─► edges + PageRank ─► author_scores
                 (daily cron, Railway)            (post_topics)      (per topic)        (daily snapshot)
                                                                                           │
                         browser ─► Next.js (Vercel, Clerk) ─► FastAPI (Railway) ◄─────────┘
                                    server-side fetch + X-Internal-Key
```

## How the score works

For each topic, over the trailing `window_days` (default 30), for every account with at least
one on-topic post:

| Component | What it measures | Weight |
|---|---|---|
| **Centrality** | PageRank over the topic's citation graph (quote 3, repost 2, reply/mention/link 1), as a percentile within the topic | 0.5 |
| **Engagement** | log(likes + 2·reposts + 3·quotes + replies) on their on-topic posts, percentile | 0.3 |
| **Reach** | log(followers / subscribers), percentile | 0.2 |

Only posts the classifier marks on-topic with confidence ≥ 0.6 count. Weights and the
threshold are env settings. "Rising" is today's score minus the score 7 days ago.

## Local development

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 and Docker.

```bash
make install            # uv sync + npm ci
make db                 # Postgres 16 + pgvector on :5432 (creates influence and influence_test)
cp .env.example .env    # fill in whichever API keys you have
make migrate topics     # schema + load topics/*.yaml
make seed               # optional: synthetic demo data so the UI has something to show
make api                # FastAPI on :8000
cp web/.env.example web/.env.local   # API_URL=http://localhost:8000, same INTERNAL_API_KEY
make web                # UI on :3000; with no Clerk keys in dev, auth is skipped
```

Run pieces of the pipeline:

```bash
uv run python -m app.jobs.collect --topic pa-energy --platform bluesky --dry-run   # print, don't write
uv run python -m app.jobs.collect --platform x --record tests/fixtures/recorded/   # save raw responses
uv run python -m app.jobs.classify --limit 40
uv run python -m app.jobs.score
make run                # everything, as the cron does
make check              # lint, types, tests
```

### Topics

One YAML file per topic in `topics/` (see `topics/example.yaml`). `description` is what the
classifier judges relevance against, so be specific about geography, actors and exclusions.
`keywords` are only search queries. `seed_accounts` (`x:handle`, `bluesky:handle`,
`youtube:@handle`, `podcast:<feed url>`) are always collected. `sync_topics` (run on every
deploy and at the start of each daily run) upserts them; set `active: false` to pause one.

The two starting topics (`pa-energy`, `allegheny-reassessment`) are drafts: review keywords,
subreddits and seed accounts before the first real run.

## API credentials

| Platform | Get it at | Env vars | Notes |
|---|---|---|---|
| X | developer.x.com (ColdSpark-owned account, Basic tier) | `X_BEARER_TOKEN`, `X_MONTHLY_POST_BUDGET` | set the budget to ~80% of the tier's monthly read cap |
| Bluesky | Settings → App passwords | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` | |
| Reddit | reddit.com/prefs/apps → "script" app | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | |
| YouTube | Google Cloud console → YouTube Data API v3 key | `YOUTUBE_API_KEY` | 10k units/day; searches capped at 30/day |
| Podcasts | api.podcastindex.org | `PODCASTINDEX_KEY`, `PODCASTINDEX_SECRET` | |
| News | none (GDELT is open) | `NEWS_RSS_FEEDS` (optional) | |
| Classifier | Anthropic console | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | any OpenAI-compatible endpoint works |
| Embeddings | OpenAI | `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL` | |

A collector with no credentials is skipped; `ENABLED_COLLECTORS` turns collectors off
explicitly. A failing collector is recorded on the Status page and never blocks the others.

**Spend guardrails:** X reads are split evenly across days left in the month and active
topics, and stop when a topic's daily share is used (Status page shows the month's use).
LLM classification stops for the day at `LLM_DAILY_TOKEN_CAP` tokens.

## Deploy

### 1. Database: Supabase

1. Create a project. In **Database → Extensions** enable `vector`.
2. Copy the **pooled** connection string (Transaction pooler, port 6543). That's
   `DATABASE_URL` everywhere below; `postgres://…` is fine, the app rewrites the driver.
3. From your machine: `DATABASE_URL=<pooled url> uv run alembic upgrade head` (Railway
   also runs this on every API deploy).

### 2. API + daily job: Railway

1. New project → **Deploy from GitHub repo**. This first service is the API: it picks up
   `railway.toml` (migrations + topic sync pre-deploy, `uvicorn`, healthcheck on `/health`).
2. Variables: everything in `.env.example` with real values. Generate `INTERNAL_API_KEY`
   with `openssl rand -hex 32`.
3. **Settings → Networking → Generate domain**. Check `https://<domain>/health` returns
   `{"status":"ok","db":"ok"}`.
4. Add a second service from the same repo for the daily pipeline. In its **Settings →
   Config-as-code** set the path to `railway.cron.toml` (start command
   `python -m app.jobs.run_all`, cron `0 10 * * *` = 6am EDT / 5am EST). Give it the same
   variables (Railway "shared variables" saves duplicating them).

### 3. Web: Vercel + Clerk

1. In Clerk: create an application with **Google** as the only sign-in method. Under
   **Restrictions**, enable the allowlist and add `coldspark.com` (or individual addresses).
   Turn off sign-ups for anyone else.
2. In Vercel: import the repo with **Root Directory = `web`**. Env vars: `API_URL` (the
   Railway domain), `INTERNAL_API_KEY` (same value), `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`,
   `CLERK_SECRET_KEY`, and `ALLOWED_EMAIL_DOMAINS=coldspark.com`.
3. Deploy. Signing in with a non-ColdSpark Google account must be refused: Clerk refuses it,
   and the app re-checks the email domain server-side on every page and action.

The FastAPI service is not meant for browsers: every route except `/health` returns 401
without `X-Internal-Key`, and only the Next.js server holds the key.

## Repo layout

```
app/
  config.py, db.py, models.py, schemas.py, llm.py, topics.py, log.py
  collectors/   base.py (Collector ABC, persistence, retries), x.py + x_budget.py,
                bluesky.py, reddit.py, youtube.py, news.py, podcasts.py
  pipeline/     classify.py, embed.py, graph.py, score.py
  jobs/         sync_topics, collect, classify, embed, score, run_all, seed_demo
  api/          main.py, routers/{topics,authors,persons,posts,stats}.py
alembic/        migrations (hand-written SQL)
topics/         topic YAML
tests/          pytest; fixtures/ (API responses), eval/ (classifier eval harness)
web/            Next.js 15 app router, Tailwind, Clerk, Recharts
```
