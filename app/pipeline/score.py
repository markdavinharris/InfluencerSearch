"""Composite influence score per author per topic per day.

For each author with >= 1 on-topic post in the topic window:
- centrality = PageRank on the topic graph, percentile-ranked within topic (0-1)
- engagement = log1p(likes + 2*reposts + 3*quotes + replies) over on-topic posts, percentile
- reach      = log1p(followers), percentile
- composite  = w_c*centrality + w_e*engagement + w_r*reach  (weights from settings)

Snapshots are upserted for `as_of`, never deleted. "Rising" is computed at query time.
"""

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy.stats import rankdata
from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import Settings, get_settings
from app.log import get_logger
from app.models import author_scores, authors, post_topics, posts
from app.pipeline.graph import (
    build_graph,
    on_topic_filter,
    pagerank,
    resolve_edges,
    save_edges,
    window_bounds,
)
from app.schemas import Topic

log = get_logger(__name__)


@dataclass
class AuthorStats:
    author_id: int
    on_topic_posts: int
    likes: int = 0
    reposts: int = 0
    quotes: int = 0
    replies: int = 0
    followers: int | None = None


@dataclass
class ScoreRow:
    author_id: int
    centrality: float
    engagement: float
    reach: float
    composite: float
    rank: int
    on_topic_posts: int
    pagerank: float


def percentile(values: list[float]) -> list[float]:
    """Percentile rank in [0, 1]; ties share the average rank; a single value -> 1.0."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    ranks = rankdata(np.asarray(values, dtype=float), method="average")
    return [float((r - 1) / (n - 1)) for r in ranks]


def compute_scores(
    stats: list[AuthorStats], pr: dict[int, float], settings: Settings | None = None
) -> list[ScoreRow]:
    s = settings or get_settings()
    if not stats:
        return []
    raw_pr = [pr.get(a.author_id, 0.0) for a in stats]
    raw_eng = [math.log1p(a.likes + 2 * a.reposts + 3 * a.quotes + a.replies) for a in stats]
    raw_reach = [math.log1p(a.followers or 0) for a in stats]
    cen, eng, rea = percentile(raw_pr), percentile(raw_eng), percentile(raw_reach)
    rows = [
        ScoreRow(
            author_id=a.author_id,
            centrality=cen[i],
            engagement=eng[i],
            reach=rea[i],
            composite=(
                s.score_w_centrality * cen[i]
                + s.score_w_engagement * eng[i]
                + s.score_w_reach * rea[i]
            ),
            rank=0,
            on_topic_posts=a.on_topic_posts,
            pagerank=raw_pr[i],
        )
        for i, a in enumerate(stats)
    ]
    # Rank by composite; break ties by raw PageRank then post count then id for stability.
    rows.sort(key=lambda r: (-r.composite, -r.pagerank, -r.on_topic_posts, r.author_id))
    for i, r in enumerate(rows, start=1):
        r.rank = i
    return rows


def author_stats(conn: Connection, topic: Topic, as_of: date) -> list[AuthorStats]:
    start, end = window_bounds(as_of, topic.window_days)
    q = (
        select(
            posts.c.author_id,
            func.count().label("n"),
            func.sum(posts.c.likes).label("likes"),
            func.sum(posts.c.reposts).label("reposts"),
            func.sum(posts.c.quotes).label("quotes"),
            func.sum(posts.c.replies).label("replies"),
            func.max(authors.c.followers).label("followers"),
        )
        .select_from(
            posts.join(post_topics, post_topics.c.post_id == posts.c.id).join(
                authors, authors.c.id == posts.c.author_id
            )
        )
        .where(on_topic_filter(topic.id), posts.c.published_at >= start, posts.c.published_at < end)
        .group_by(posts.c.author_id)
    )
    return [
        AuthorStats(
            author_id=r.author_id,
            on_topic_posts=r.n,
            likes=r.likes or 0,
            reposts=r.reposts or 0,
            quotes=r.quotes or 0,
            replies=r.replies or 0,
            followers=r.followers,
        )
        for r in conn.execute(q)
    ]


def save_scores(conn: Connection, topic_id: int, as_of: date, rows: list[ScoreRow]) -> None:
    if not rows:
        return
    values = [
        {
            "topic_id": topic_id,
            "author_id": r.author_id,
            "as_of": as_of,
            "centrality": r.centrality,
            "engagement": r.engagement,
            "reach": r.reach,
            "composite": r.composite,
            "rank": r.rank,
            "on_topic_posts": r.on_topic_posts,
            "pagerank": r.pagerank,
        }
        for r in rows
    ]
    # Re-scoring the same day replaces that day's snapshot.
    conn.execute(
        author_scores.delete().where(
            author_scores.c.topic_id == topic_id, author_scores.c.as_of == as_of
        )
    )
    stmt = pg_insert(author_scores).values(values)
    conn.execute(stmt)


def score_topic(conn: Connection, topic: Topic, as_of: date) -> dict[str, int]:
    edges = resolve_edges(conn, topic, as_of)
    save_edges(conn, topic.id, edges)
    stats = author_stats(conn, topic, as_of)
    g = build_graph(edges, nodes=[a.author_id for a in stats])
    pr = pagerank(g)
    rows = compute_scores(stats, pr)
    save_scores(conn, topic.id, as_of, rows)
    conn.commit()
    result = {"authors": len(rows), "edges": len(edges), "graph_nodes": g.number_of_nodes()}
    log.info("score_topic_done", topic=topic.slug, as_of=str(as_of), **result)
    return result
