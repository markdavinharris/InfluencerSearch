# Topic Influence Mapper — Build Plan

This document is the starting brief for Claude Code. Read it fully before writing code. Work phase by phase; each phase has acceptance criteria that must pass before moving on. Commit at the end of each phase.

## 1. What we're building

An internal tool for ColdSpark that, given a topic (e.g. "Pennsylvania energy policy", "Allegheny County property reassessment"), collects public discussion from several platforms, filters it to posts that are actually about the topic, builds a graph of who quotes/reposts/mentions whom, and ranks the accounts that matter most on that topic. Output is a web UI with a ranked influencer table per topic, author detail pages, and a "rising" view.

Perfection is not the goal. A working end-to-end pipeline on real data in three weeks is.

## 2. Decisions already made (don't relitigate)

- **Cloud from day one.** Postgres on Supabase (pgvector enabled), FastAPI + scheduled jobs on Railway, Next.js on Vercel. Local dev uses `docker compose` for Postgres only. Nothing depends on a specific machine being awake.
- **Language:** Python 3.12 for everything server-side (collectors, scoring, API). TypeScript/Next.js for the UI.
- **Database:** Postgres 16 with pgvector (Supabase in prod, Docker locally). No ORM magic — use SQLAlchemy Core or plain SQL with `psycopg`. Alembic for migrations. Connect via Supabase's pooled connection string.
- **Platforms in v1:** X, Bluesky, Reddit, YouTube, news (GDELT + RSS), podcasts (Podcast Index + RSS). Not Facebook, Instagram, or TikTok.
- **X is in from Phase 1,** on the Basic tier (~$200/mo). Budget guardrails are built alongside the collector, not later. Plan for 2–3 active topics on Basic. The developer account is a ColdSpark-owned login, not a personal one.
- **LLM access:** all LLM and embedding calls go through an OpenAI-compatible client with a configurable `base_url` and `model`. Default: Anthropic Claude Haiku for classification, OpenAI `text-embedding-3-small` (1536 dims) for embeddings. Never hardcode a provider; swapping must be an env change.
- **Orchestration:** plain Python entrypoints (`python -m app.jobs.run_all`) run as a Railway cron job daily. No Airflow/Prefect in v1.
- **Auth:** Clerk on the Next.js app, Google sign-in restricted to `@coldspark.com` (allowlist can be a single address at first). FastAPI is **not** publicly callable: it requires an `X-Internal-Key` header that only the Next.js server has (BFF pattern). All UI data fetching happens in server components / route handlers.
- **Entity resolution across platforms:** manual. One author row per platform account; a `persons` table lets a human merge accounts via the UI. No automated matching in v1.

## 3. Repo layout

```
influence-mapper/
  docker-compose.yml          # local postgres+pgvector for dev only
  railway.toml                # api service + cron job definitions
  .env.example
  pyproject.toml              # uv or pip-tools; pin everything
  alembic/
  app/
    config.py                 # pydantic-settings, reads .env
    db.py                     # engine, session helpers
    models.py                 # table definitions (SQLAlchemy Core)
    schemas.py                # pydantic models shared by API + jobs
    llm.py                    # OpenAI-compatible client wrapper, retry, cost logging
    collectors/
      base.py                 # Collector ABC, normalized Post/Author/Edge dataclasses
      x.py
      bluesky.py
      reddit.py
      youtube.py
      news.py                 # GDELT + RSS
      podcasts.py
    pipeline/
      classify.py             # on-topic / stance / summary
      embed.py
      graph.py                # build edge graph, PageRank
      score.py                # composite score + snapshot
    jobs/
      collect.py              # run all collectors for all active topics
      classify.py
      score.py
      run_all.py
    api/
      main.py                 # FastAPI app
      routers/{topics,authors,scores,persons}.py
  web/                        # Next.js 15, app router, Tailwind, shadcn/ui, Clerk
    middleware.ts             # Clerk auth, @coldspark.com only
    lib/api.ts                # server-only fetch wrapper adding X-Internal-Key
  topics/
    example.yaml              # topic definition format
  tests/
  PLAN.md                     # this file
  CLAUDE.md                   # conventions for Claude Code (write in Phase 0)
```

## 4. Topic definition

Topics are YAML files in `topics/`, loaded into the `topics` table by a `python -m app.jobs.sync_topics` command. Example:

```yaml
slug: pa-energy
name: Pennsylvania Energy Policy
description: >
  Discussion of energy policy in Pennsylvania: natural gas, fracking, RGGI,
  electricity prices, PJM, pipelines, data center power demand, PUC decisions.
  Includes state legislation and regulatory actions. Excludes national energy
  debates unless they explicitly concern PA.
keywords:            # used for platform search queries
  - "Pennsylvania energy"
  - "PA fracking"
  - RGGI
  - "PJM capacity"
  - "Shapiro energy plan"
exclude_keywords:
  - "Pennsylvania Avenue"
subreddits: [Pennsylvania, pittsburgh, philadelphia, energy]
seed_accounts:       # optional, platform:handle; always collected regardless of search
  - x:somereporter
  - bluesky:someone.bsky.social
window_days: 30
active: true
```

The `description` is what the LLM classifier uses. Keywords are only for retrieval.

## 5. Data model

```sql
topics (
  id serial pk, slug text unique, name text, description text,
  config jsonb,            -- the full YAML
  window_days int default 30, active bool, created_at, updated_at
)

persons (                  -- a human/org, possibly spanning platforms; created by manual merge
  id serial pk, display_name text, notes text, created_at
)

authors (
  id serial pk,
  platform text,           -- x | bluesky | reddit | youtube | news | podcast
  platform_id text,        -- stable id on that platform
  handle text, display_name text, bio text, url text, avatar_url text,
  followers int, following int,
  person_id int null references persons,
  raw jsonb, first_seen timestamptz, last_seen timestamptz,
  unique (platform, platform_id)
)

posts (
  id bigserial pk,
  platform text, platform_id text,
  author_id int references authors,
  text text, url text, published_at timestamptz,
  likes int, reposts int, replies int, quotes int, views int,
  kind text,               -- post | reply | repost | quote | article | video | episode
  parent_platform_id text null,      -- for replies/quotes/reposts: what it points at
  raw jsonb, collected_at timestamptz,
  embedding vector(1536) null,
  unique (platform, platform_id)
)

post_topics (              -- classifier output; a post can hit several topics
  post_id bigint references posts, topic_id int references topics,
  on_topic bool, confidence real, stance text, summary text,
  model text, classified_at timestamptz,
  primary key (post_id, topic_id)
)

edges (                    -- derived from posts; rebuilt per scoring run, but persisted for inspection
  id bigserial pk,
  topic_id int, src_author_id int, dst_author_id int,
  kind text,               -- quote | repost | reply | mention | link
  post_id bigint, occurred_at timestamptz
)

author_scores (            -- one row per author per topic per scoring day
  topic_id int, author_id int, as_of date,
  centrality real, engagement real, reach real, composite real,
  rank int, on_topic_posts int,
  primary key (topic_id, author_id, as_of)
)

collector_runs (
  id serial pk, topic_id int, platform text, started_at, finished_at,
  fetched int, inserted int, error text null
)

llm_calls (                -- cost tracking
  id bigserial pk, purpose text, model text, input_tokens int, output_tokens int, created_at
)
```

Indexes: `posts(published_at)`, `posts(author_id)`, `post_topics(topic_id, on_topic)`, `edges(topic_id, dst_author_id)`, `author_scores(topic_id, as_of, rank)`.

## 6. Collectors

All collectors implement:

```python
class Collector(ABC):
    platform: str

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]: ...
```

Each yields normalized `Post` dataclasses (with an embedded `Author` and a list of `EdgeHint`s — what this post quotes/reposts/replies to/mentions, as platform ids or handles). The base class handles upserting authors and posts and recording a `collector_runs` row. Collectors must be idempotent: re-running for the same window inserts nothing new (engagement counts are refreshed). Edge hints are persisted on `posts.edge_hints`; authors referenced by a post whose profile came in the same response (`related_authors`) are upserted so edges to them resolve. Each (post, topic) retrieval is recorded in `post_sources`, and classification runs only on those pairs.

Per-platform notes:

- **X** — `tweepy` Client, API v2 `search_recent_tweets` with `expansions=author_id,referenced_tweets.id,entities.mentions.username` and `tweet.fields=public_metrics,created_at,referenced_tweets,entities`. Basic tier has a monthly post-read cap (confirm the current number at signup; set `X_MONTHLY_POST_BUDGET` to ~80% of it). **Budget guardrails are part of this collector, not an afterthought:** track reads in a `x_budget` table (month, posts_read), split the monthly budget evenly across active topics and days remaining, pull `seed_accounts` timelines first (cheap, high value), then spend the remainder on search. Stop and log when the daily allocation is hit. Build one OR-joined query per topic from keywords; keep retweets (they're useful edges) unless budget is tight. Expose budget status on `/stats`. Implementation: per-topic daily spend is tracked in `api_quota(platform='x', scope=<slug>, day)` so a second run in the same day can't double-spend; the per-topic allocation is computed from the budget remaining at the *start* of the day so topic order doesn't matter; every tweet object returned (`data` + `includes.tweets`) counts as a read.
- **Bluesky** — XRPC over `httpx` (changed from the `atproto` SDK during Phase 1 so recorded fixtures are exact wire JSON and normalization is plain dict handling), `app.bsky.feed.searchPosts` per keyword, plus `getAuthorFeed` for seeds; follower counts via batched `getProfiles`. Free, generous limits. Quotes/replies/mentions are in the record's `embed`, `reply`, and `facets`.
- **Reddit** — Reddit's OAuth JSON API over `httpx` with app-only auth (changed from `praw` in Phase 1, same credentials, for the same fixture reason as Bluesky). Search (`t=month`) for each keyword across topic subreddits (as one multireddit) plus `all`. Submissions are posts; for the 25 busiest threads, top-level comments and their direct replies are stored as `reply` posts with `reply` edges (storing the replies as posts lets edges be built uniformly from on-topic posts). Author = redditor; reach = profile follower count (`/user/{name}/about`, capped at 100 lookups/run). Link posts add a `link` edge to the linked domain's news author.
- **YouTube** — Data API v3: `search.list` (type=video, publishedAfter), `videos.list` for statistics, `channels.list` for subscriber counts. Author = channel. Quota is 10k units/day; `search.list` costs 100, so cap at ~30 searches/day across all topics. Skip comments in v1.
- **News** — GDELT DOC 2.0 API (`https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&format=json`) for articles; author = outlet domain. Plus a configurable RSS list (local outlets, Substacks) via `feedparser`; author = feed. Pull article text with `trafilatura` for classification.
- **Podcasts** — Podcast Index API search by term, then `feedparser` on the feed URL for recent episodes; classify on title + description only (no transcription in v1). Author = show.

Rate limiting and retries live in `base.py` (use `tenacity`). Every collector gets a `--dry-run` flag that prints normalized posts without writing.

## 7. Classification

`pipeline/classify.py` selects posts with no `post_topics` row for an active topic, batches them, and calls the LLM with the topic `description` and up to ~20 posts per call. Output is strict JSON per post:

```json
{"post_id": 123, "on_topic": true, "confidence": 0.9, "stance": "oppose|support|neutral|unclear", "summary": "one line"}
```

Use a system prompt that insists on JSON only; validate with pydantic; on parse failure retry once with a smaller batch. Log every call to `llm_calls`. A post is only used downstream if `on_topic and confidence >= 0.6` (config).

Pre-filter before the LLM: skip posts shorter than 15 chars, skip exact-duplicate text, skip anything matching `exclude_keywords`. This cuts volume substantially.

Embeddings (`pipeline/embed.py`) are generated for on-topic posts only, via the same OpenAI-compatible client (`/v1/embeddings`). Default model `text-embedding-3-small` (1536 dims). Used in v1 only for the "similar posts" panel in the UI; keep it simple.

## 8. Graph and scoring

`pipeline/graph.py` builds, per topic, a directed weighted graph over authors from `edges` in the trailing `window_days`, where edges derive from on-topic posts: if author A's on-topic post quotes/reposts/replies-to/mentions author B, add edge A→B. Weights: quote 3, repost 2, reply 1, mention 1, link (news article linking to an outlet/author) 1. Cross-platform edges only exist where handles are explicit (e.g. a Bluesky post mentioning an X handle), which is rare; don't try to be clever.

Run `networkx.pagerank(G, weight="weight", alpha=0.85)`.

`pipeline/score.py` computes for each author with ≥1 on-topic post in the window:

- `centrality` = PageRank, percentile-ranked within topic (0–1)
- `engagement` = log1p(sum of likes + 2·reposts + 3·quotes + replies on on-topic posts), percentile-ranked within topic
- `reach` = log1p(followers or subscribers), percentile-ranked within topic
- `composite` = 0.5·centrality + 0.3·engagement + 0.2·reach

Weights are config values. Write one `author_scores` row per author per topic for today's date, with `rank` by composite desc. Keep history; never delete old snapshots.

Also compute and store nothing else fancy. "Rising" is computed at query time as composite(today) − composite(7 days ago).

## 9. API (FastAPI)

- `GET /topics` — list with post counts and last scored date
- `GET /topics/{slug}/influencers?as_of=&limit=50&platform=` — ranked table with score components, follower count, on-topic post count, 7-day delta
- `GET /topics/{slug}/rising?days=7` — biggest positive deltas
- `GET /authors/{id}` — profile, all topics they score on, recent on-topic posts with summaries/stance, top inbound edges (who cites them) and outbound (who they cite)
- `GET /authors/{id}/score-history?topic=` — for sparklines
- `POST /persons/merge` — body `{author_ids: [...], display_name}`; sets `person_id`
- `GET /posts/{id}/similar` — pgvector cosine top-10 within topic
- `GET /health`, `GET /stats` — collector runs, LLM spend

Return pydantic models. Every route except `/health` requires `X-Internal-Key` matching `INTERNAL_API_KEY`; reject otherwise with 401. No CORS needed — the browser never calls this API directly.

## 10. Web UI (Next.js)

Four pages, nothing more in v1:

1. **Topics** — cards with name, posts in window, last scored, top 3 names.
2. **Topic → Influencers** — sortable table: rank, avatar + name + platform badge, composite with 30-day sparkline, centrality / engagement / reach as small bars, followers, on-topic posts, 7d delta. Filters: platform, min posts. Toggle to "Rising" view.
3. **Author** — header with bio/links/followers; score components per topic; recent on-topic posts (text, summary, stance tag, engagement, link out); "cited by" and "cites" lists with counts; merge-into-person control.
4. **Status** — collector runs table, errors, LLM spend by day, X API budget used this month.

Use shadcn/ui table, badge, card, tabs; Recharts for sparklines. Server components fetch from the FastAPI URL via `lib/api.ts`, which adds the internal key. Clerk middleware protects every route; sign-in restricted to Google accounts on `@coldspark.com` (configure in the Clerk dashboard; start with an allowlist of one address if preferred). Keep styling default-neutral; ColdSpark branding can come later.

## 11. Phases and acceptance criteria

**Phase 0 — Scaffold + cloud wiring (days 1–2)**
Repo, `docker-compose.yml` with pgvector for local dev, pyproject with pinned deps, `config.py`, `db.py`, Alembic initial migration creating all tables in §5, `topics/` with the two real starting topics (Mark supplies), `sync_topics` job, `CLAUDE.md` with conventions (run tests with `pytest`, migrations with `alembic`, never commit `.env`, how to deploy). Create the Supabase project, enable pgvector, run migrations against it. Create the Railway project with the API service and a placeholder cron job. Write `CLAUDE.md` so future sessions don't need this whole document.
*Done when:* `alembic upgrade head && python -m app.jobs.sync_topics` succeeds against **both** local Docker and Supabase, and `/health` responds on the Railway URL.

**Phase 1 — X + Bluesky + Reddit collectors (days 3–6)**
Implement `base.py`, then `x.py` **with the budget guardrails from §6**, then `bluesky.py` and `reddit.py`, and `jobs/collect.py`. Unit tests for normalization and for the X budget allocator using recorded fixtures (save real API responses as JSON in `tests/fixtures/`). Run the first real X pull with a deliberately low budget cap and check consumption on the X developer dashboard matches `x_budget`.
*Done when:* running collect twice for a real topic inserts posts, authors, and edge hints the first time and zero new rows the second time; `collector_runs` reflects both; X budget accounting matches the dashboard within 5%.

**Phase 2 — Classification (days 7–8)**
`llm.py` wrapper with retries and `llm_calls` logging; `pipeline/classify.py`; `jobs/classify.py`. Default to Haiku. Record precision on a hand-labeled sample of 50 posts in `tests/eval/`; also run the eval once against a cheaper/other model so the provider switch is proven.
*Done when:* ≥85% agreement with hand labels on on-topic/off-topic, and swapping `LLM_BASE_URL`/`LLM_MODEL` requires no code change.

**Phase 3 — Graph + scoring (days 9–10)**
`graph.py`, `score.py`, `jobs/score.py`. Test PageRank on a tiny synthetic graph with a known answer.
*Done when:* `author_scores` has a ranked snapshot for today and the top 10 for each starting topic pass a sniff test (real reporters/officials/advocates, not spam).

**Phase 4 — API (days 11–12)**
All endpoints in §9 with the internal-key check and tests via `httpx` test client. Deploy to Railway.
*Done when:* every endpoint returns valid JSON against Supabase, unauthenticated requests get 401, and `/topics/{slug}/influencers` returns in <300ms from Railway.

**Phase 5 — UI (days 13–16)**
Four pages in §10, Clerk auth, deployed to Vercel.
*Done when:* signing in with a non-coldspark Google account is refused; a teammate with access can open the topic page, understand why #1 is #1 from the score bars, click into them, and read their posts without explanation.

**Phase 6 — Remaining collectors (days 17–19)**
YouTube, news, podcasts. Add each behind a config flag so a failing collector never blocks the others.
*Done when:* all six platforms contribute posts to at least one topic.

**Phase 7 — Scheduling + hardening (days 20–21)**
`run_all.py` (collect → classify → embed → score) as a Railway cron job at 6am ET daily, structured logging, a `make` or `just` file, README with setup and deploy steps. Run it unattended for three days.
*Done when:* three consecutive scheduled runs complete with no manual intervention, the influencer table changes day to day, and X budget consumption is on track for the month.

## 12. Environment variables

```
DATABASE_URL=            # local: postgresql+psycopg://app:app@localhost:5432/influence ; prod: Supabase pooled URL
INTERNAL_API_KEY=        # shared secret between Next.js server and FastAPI
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_API_KEY=
LLM_MODEL=               # current Haiku model id
EMBED_BASE_URL=https://api.openai.com/v1
EMBED_API_KEY=
EMBED_MODEL=text-embedding-3-small
X_BEARER_TOKEN=
X_MONTHLY_POST_BUDGET=   # ~80% of the Basic tier cap confirmed at signup
LLM_DAILY_TOKEN_CAP=2000000

# web/.env
API_URL=                 # Railway URL of FastAPI
INTERNAL_API_KEY=        # same value as above
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY= / CLERK_SECRET_KEY=
BLUESKY_HANDLE= / BLUESKY_APP_PASSWORD=
REDDIT_CLIENT_ID= / REDDIT_CLIENT_SECRET= / REDDIT_USER_AGENT=
YOUTUBE_API_KEY=
PODCASTINDEX_KEY= / PODCASTINDEX_SECRET=
ON_TOPIC_MIN_CONFIDENCE=0.6
SCORE_W_CENTRALITY=0.5 / SCORE_W_ENGAGEMENT=0.3 / SCORE_W_REACH=0.2
```

## 13. Conventions for Claude Code

- Small, reviewable commits per phase. Commit message states which phase and acceptance criterion it advances.
- Tests for normalization and scoring logic; don't test third-party APIs live in CI — use fixtures.
- Type hints everywhere; `ruff` for lint/format; `mypy` in non-strict mode.
- Never swallow exceptions in collectors — record to `collector_runs.error` and continue to the next platform.
- Log LLM spend; if a run exceeds a configured daily token cap, stop classifying and alert via log, don't silently continue.
- When something in this plan turns out wrong on contact with a real API, change the plan file and say why in the commit, rather than working around it silently.

## 14. Explicitly out of scope for v1

Facebook/Instagram/TikTok; automated cross-platform identity matching; podcast transcription; alerts/notifications; multi-user auth; client-facing exports; sentiment beyond the simple stance tag; anything real-time (daily batch is fine).

## 15. Open questions to resolve during the build

- Whether X Basic tier search volume is enough for 3 topics at once, or whether topics need to rotate or the Pro tier is justified. Measure in Phase 1 and record the answer here.
- Whether a cheaper model than Haiku classifies political content well enough. Phase 2 eval decides.
- When team access widens beyond the initial allowlist, and whether anyone needs edit rights (person merges) vs read-only.
- Whether `reach` should be down-weighted further; initial top-10s will reveal if big generic accounts crowd out topic specialists.
