from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, text

from app.api.deps import get_conn
from app.api.queries import AUTHOR_COLS, author_ref, comparison_date, latest_as_of, scores_at
from app.config import get_settings
from app.schemas import (
    InfluencerRow,
    InfluencersResponse,
    RisingResponse,
    RisingRow,
    TopicSummary,
    TopicTopAuthor,
)

router = APIRouter(prefix="/topics", tags=["topics"])
Conn = Annotated[Connection, Depends(get_conn)]


def _topic_id(conn: Connection, slug: str) -> int:
    tid = conn.execute(text("SELECT id FROM topics WHERE slug = :s"), {"s": slug}).scalar()
    if tid is None:
        raise HTTPException(404, f"unknown topic {slug!r}")
    return int(tid)


@router.get("", response_model=list[TopicSummary])
def list_topics(conn: Conn) -> list[TopicSummary]:
    min_conf = get_settings().on_topic_min_confidence
    rows = conn.execute(
        text("""
        SELECT t.id, t.slug, t.name, t.description, t.active, t.window_days,
          (SELECT count(*) FROM post_sources ps JOIN posts p ON p.id = ps.post_id
             WHERE ps.topic_id = t.id
               AND p.published_at >= now() - make_interval(days => t.window_days)) AS posts,
          (SELECT count(*) FROM post_topics pt JOIN posts p ON p.id = pt.post_id
             WHERE pt.topic_id = t.id AND pt.on_topic AND pt.confidence >= :mc
               AND p.published_at >= now() - make_interval(days => t.window_days)) AS on_topic,
          (SELECT max(as_of) FROM author_scores s WHERE s.topic_id = t.id) AS last_scored
        FROM topics t ORDER BY t.active DESC, t.name
        """),
        {"mc": min_conf},
    ).all()
    out = []
    for r in rows:
        top = []
        if r.last_scored:
            top = [
                TopicTopAuthor(
                    author_id=a.id,
                    platform=a.platform,
                    name=a.display_name or a.handle or f"#{a.id}",
                )
                for a in conn.execute(
                    text("""SELECT a.id, a.platform, a.display_name, a.handle
                            FROM author_scores s JOIN authors a ON a.id = s.author_id
                            WHERE s.topic_id = :t AND s.as_of = :d ORDER BY s.rank LIMIT 3"""),
                    {"t": r.id, "d": r.last_scored},
                )
            ]
        out.append(
            TopicSummary(
                id=r.id,
                slug=r.slug,
                name=r.name,
                description=r.description,
                active=r.active,
                window_days=r.window_days,
                posts_in_window=r.posts,
                on_topic_posts_in_window=r.on_topic,
                last_scored=r.last_scored,
                top_authors=top,
            )
        )
    return out


@router.get("/{slug}/influencers", response_model=InfluencersResponse)
def influencers(
    slug: str,
    conn: Conn,
    as_of: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    platform: str | None = None,
    min_posts: Annotated[int, Query(ge=0)] = 0,
) -> InfluencersResponse:
    tid = _topic_id(conn, slug)
    snap = latest_as_of(conn, tid, as_of)
    if snap is None:
        return InfluencersResponse(topic=slug, as_of=None, rows=[])
    rows = conn.execute(
        text(f"""
        SELECT s.rank, s.composite, s.centrality, s.engagement, s.reach, s.on_topic_posts,
               {AUTHOR_COLS}
        FROM author_scores s JOIN authors a ON a.id = s.author_id
        WHERE s.topic_id = :t AND s.as_of = :d
          AND (CAST(:p AS text) IS NULL OR a.platform = :p)
          AND s.on_topic_posts >= :mp
        ORDER BY s.rank LIMIT :lim
        """),
        {"t": tid, "d": snap, "p": platform, "mp": min_posts, "lim": limit},
    ).all()
    ids = [r.a_id for r in rows]
    prev_date = comparison_date(conn, tid, snap, 7)
    prev = scores_at(conn, tid, prev_date) if prev_date else {}
    spark: dict[int, list[float]] = {i: [] for i in ids}
    if ids:
        for h in conn.execute(
            text("""SELECT author_id, composite FROM author_scores
                    WHERE topic_id = :t AND author_id = ANY(:ids)
                      AND as_of > :start AND as_of <= :d ORDER BY as_of"""),
            {"t": tid, "ids": ids, "start": snap - timedelta(days=30), "d": snap},
        ):
            spark[h.author_id].append(round(h.composite, 4))
    return InfluencersResponse(
        topic=slug,
        as_of=snap,
        rows=[
            InfluencerRow(
                rank=r.rank,
                author=author_ref(r),
                composite=r.composite,
                centrality=r.centrality,
                engagement=r.engagement,
                reach=r.reach,
                on_topic_posts=r.on_topic_posts,
                delta_7d=(r.composite - prev[r.a_id]) if r.a_id in prev else None,
                sparkline=spark.get(r.a_id, []),
            )
            for r in rows
        ],
    )


@router.get("/{slug}/rising", response_model=RisingResponse)
def rising(
    slug: str,
    conn: Conn,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    as_of: date | None = None,
) -> RisingResponse:
    """Biggest positive composite change vs. `days` ago. Authors new since then are compared
    against 0."""
    tid = _topic_id(conn, slug)
    snap = latest_as_of(conn, tid, as_of)
    if snap is None:
        return RisingResponse(topic=slug, as_of=None, compared_to=None, rows=[])
    prev_date = comparison_date(conn, tid, snap, days)
    if prev_date is None:  # no history yet: nothing to compare against
        return RisingResponse(topic=slug, as_of=snap, compared_to=None, rows=[])
    rows = conn.execute(
        text(f"""
        SELECT s.rank, s.composite, p.composite AS prev, s.composite - coalesce(p.composite, 0)
               AS delta, {AUTHOR_COLS}
        FROM author_scores s
        JOIN authors a ON a.id = s.author_id
        LEFT JOIN author_scores p ON p.topic_id = s.topic_id AND p.author_id = s.author_id
                                  AND p.as_of = :pd
        WHERE s.topic_id = :t AND s.as_of = :d
          AND s.composite - coalesce(p.composite, 0) > 0
        ORDER BY delta DESC, s.rank LIMIT :lim
        """),
        {"t": tid, "d": snap, "pd": prev_date, "lim": limit},
    ).all()
    return RisingResponse(
        topic=slug,
        as_of=snap,
        compared_to=prev_date,
        rows=[
            RisingRow(
                author=author_ref(r),
                rank=r.rank,
                composite=r.composite,
                previous_composite=r.prev,
                delta=r.delta,
            )
            for r in rows
        ],
    )
