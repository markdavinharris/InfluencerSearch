"""Seed-account discovery and review."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import Connection, text

from app.api.deps import get_conn
from app.discovery import decide
from app.log import get_logger
from app.schemas import (
    CollectorRunRow,
    DiscoverRequest,
    DiscoverStatus,
    SeedCandidate,
    SeedCandidatesResponse,
    SeedDecision,
    TopicSeeds,
)

router = APIRouter(tags=["seeds"])
Conn = Annotated[Connection, Depends(get_conn)]
log = get_logger(__name__)

COLS = """id, platform, seed_handle, display_name, bio, url, avatar_url, followers,
    matched_posts, matched_keywords, bio_match, sample_text, sample_url, sources, score,
    status, decided_by, decided_at, last_seen"""
RUN_STALE_AFTER = timedelta(minutes=30)


def _topic(conn: Connection, slug: str) -> tuple[int, list[str]]:
    row = conn.execute(text("SELECT id, config FROM topics WHERE slug = :s"), {"s": slug}).first()
    if row is None:
        raise HTTPException(404, f"unknown topic {slug!r}")
    return row.id, list(row.config.get("seed_accounts") or [])


@router.get("/topics/{slug}/seed-candidates", response_model=SeedCandidatesResponse)
def list_candidates(
    slug: str,
    conn: Conn,
    status: Annotated[str, Query(pattern="^(pending|accepted|rejected)$")] = "pending",
    platform: str | None = None,
    min_followers: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> SeedCandidatesResponse:
    tid, _ = _topic(conn, slug)
    rows = conn.execute(
        text(f"""SELECT {COLS} FROM seed_candidates
                 WHERE topic_id = :t AND status = :st
                   AND (CAST(:p AS text) IS NULL OR platform = :p)
                   AND (platform = 'podcast' OR coalesce(followers, 0) >= :mf)
                 ORDER BY score DESC, followers DESC NULLS LAST LIMIT :lim"""),
        {"t": tid, "st": status, "p": platform, "mf": min_followers, "lim": limit},
    ).all()
    counts: dict[str, int] = {
        r.status: int(r.n)
        for r in conn.execute(
            text(
                "SELECT status, count(*) AS n FROM seed_candidates WHERE topic_id = :t GROUP BY 1"
            ),
            {"t": tid},
        )
    }
    return SeedCandidatesResponse(
        topic=slug,
        counts={s: counts.get(s, 0) for s in ("pending", "accepted", "rejected")},
        rows=[SeedCandidate(**r._mapping) for r in rows],
    )


@router.post("/seed-candidates/{candidate_id}", response_model=SeedCandidate)
def set_decision(candidate_id: int, body: SeedDecision, conn: Conn) -> SeedCandidate:
    if not decide(conn, candidate_id, body.status, body.decided_by):
        raise HTTPException(404, "candidate not found")
    row = conn.execute(
        text(f"SELECT {COLS} FROM seed_candidates WHERE id = :id"), {"id": candidate_id}
    ).one()
    conn.commit()
    return SeedCandidate(**row._mapping)


@router.get("/topics/{slug}/seeds", response_model=TopicSeeds)
def topic_seeds(slug: str, conn: Conn) -> TopicSeeds:
    tid, from_config = _topic(conn, slug)
    rows = conn.execute(
        text(f"""SELECT {COLS} FROM seed_candidates WHERE topic_id = :t AND status = 'accepted'
                 ORDER BY decided_at DESC NULLS LAST"""),
        {"t": tid},
    ).all()
    return TopicSeeds(
        topic=slug, from_config=from_config, accepted=[SeedCandidate(**r._mapping) for r in rows]
    )


def _discover_status(conn: Connection, tid: int) -> DiscoverStatus:
    runs = conn.execute(
        text("""SELECT r.id, t.slug AS topic_slug, r.platform, r.started_at, r.finished_at,
                       r.fetched, r.inserted, r.error
                FROM collector_runs r JOIN topics t ON t.id = r.topic_id
                WHERE r.topic_id = :t AND r.platform LIKE 'seeds:%'
                ORDER BY r.started_at DESC LIMIT 10"""),
        {"t": tid},
    ).all()
    now = datetime.now(UTC)
    running = any(r.finished_at is None and now - r.started_at < RUN_STALE_AFTER for r in runs)
    return DiscoverStatus(
        running=running,
        last_started=runs[0].started_at if runs else None,
        last_finished=max((r.finished_at for r in runs if r.finished_at), default=None),
        runs=[CollectorRunRow(**r._mapping) for r in runs],
    )


@router.get("/topics/{slug}/discover", response_model=DiscoverStatus)
def discover_status(slug: str, conn: Conn) -> DiscoverStatus:
    tid, _ = _topic(conn, slug)
    return _discover_status(conn, tid)


def _run_discovery_bg(slug: str, body: DiscoverRequest) -> None:
    from app.jobs.discover_seeds import run_discovery

    try:
        run_discovery([slug], list(body.sources) if body.sources else None, body.min_followers)
    except Exception:  # background task: nobody to raise to; runs table shows the failure
        log.exception("discover_background_failed", topic=slug)


@router.post("/topics/{slug}/discover", response_model=DiscoverStatus, status_code=202)
def start_discovery(
    slug: str, body: DiscoverRequest, conn: Conn, background: BackgroundTasks
) -> DiscoverStatus:
    """Start discovery for one topic in the background. 409 if one is already running."""
    tid, _ = _topic(conn, slug)
    status = _discover_status(conn, tid)
    if status.running:
        raise HTTPException(409, "discovery is already running for this topic")
    background.add_task(_run_discovery_bg, slug, body)
    status.running = True
    return status
