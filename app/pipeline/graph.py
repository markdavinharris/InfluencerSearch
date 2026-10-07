"""Per-topic citation graph over authors, and PageRank.

Edges come from the `edge_hints` of on-topic posts (on_topic and confidence >= threshold)
published in the topic's trailing window: if author A's on-topic post quotes / reposts /
replies to / mentions / links to author B, add A -> B. The `edges` table is rebuilt for the
topic on every scoring run (kept for inspection and the author page's cited-by lists).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import networkx as nx
from sqlalchemy import Connection, and_, delete, func, insert, select, tuple_

from app.config import get_settings
from app.models import authors, edges, post_topics, posts
from app.schemas import Topic

EDGE_WEIGHTS: dict[str, float] = {"quote": 3, "repost": 2, "reply": 1, "mention": 1, "link": 1}


@dataclass(frozen=True)
class Edge:
    src: int
    dst: int
    kind: str
    post_id: int | None = None
    occurred_at: datetime | None = None


def window_bounds(as_of: date, window_days: int) -> tuple[datetime, datetime]:
    end = datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=UTC)
    return end - timedelta(days=window_days), end


def on_topic_filter(topic_id: int):  # type: ignore[no-untyped-def]
    return and_(
        post_topics.c.topic_id == topic_id,
        post_topics.c.on_topic,
        post_topics.c.confidence >= get_settings().on_topic_min_confidence,
    )


def resolve_edges(conn: Connection, topic: Topic, as_of: date) -> list[Edge]:
    start, end = window_bounds(as_of, topic.window_days)
    rows = conn.execute(
        select(posts.c.id, posts.c.author_id, posts.c.edge_hints, posts.c.published_at)
        .select_from(posts.join(post_topics, post_topics.c.post_id == posts.c.id))
        .where(
            on_topic_filter(topic.id),
            posts.c.published_at >= start,
            posts.c.published_at < end,
            func.jsonb_array_length(posts.c.edge_hints) > 0,
        )
    ).all()

    by_pid: set[tuple[str, str]] = set()
    by_handle: set[tuple[str, str]] = set()
    by_post: set[tuple[str, str]] = set()
    for r in rows:
        for h in r.edge_hints:
            if h.get("author_platform_id"):
                by_pid.add((h["platform"], h["author_platform_id"]))
            if h.get("handle"):
                by_handle.add((h["platform"], h["handle"].lower()))
            if h.get("post_platform_id"):
                by_post.add((h["platform"], h["post_platform_id"]))

    pid_map: dict[tuple[str, str], int] = {}
    handle_map: dict[tuple[str, str], int] = {}
    post_map: dict[tuple[str, str], int] = {}
    for chunk in _chunks(list(by_pid)):
        for a in conn.execute(
            select(authors.c.id, authors.c.platform, authors.c.platform_id).where(
                tuple_(authors.c.platform, authors.c.platform_id).in_(chunk)
            )
        ):
            pid_map[(a.platform, a.platform_id)] = a.id
    for chunk in _chunks(list(by_handle)):
        q = select(authors.c.id, authors.c.platform, func.lower(authors.c.handle).label("h")).where(
            tuple_(authors.c.platform, func.lower(authors.c.handle)).in_(chunk)
        )
        for a in conn.execute(q):
            handle_map.setdefault((a.platform, a.h), a.id)
    for chunk in _chunks(list(by_post)):
        for p in conn.execute(
            select(posts.c.author_id, posts.c.platform, posts.c.platform_id).where(
                tuple_(posts.c.platform, posts.c.platform_id).in_(chunk)
            )
        ):
            post_map[(p.platform, p.platform_id)] = p.author_id

    out: list[Edge] = []
    for r in rows:
        seen: set[tuple[int, str]] = set()
        for h in r.edge_hints:
            plat = h["platform"]
            dst = (
                pid_map.get((plat, h.get("author_platform_id") or ""))
                or handle_map.get((plat, (h.get("handle") or "").lower()))
                or post_map.get((plat, h.get("post_platform_id") or ""))
            )
            if dst is None or dst == r.author_id or (dst, h["kind"]) in seen:
                continue
            seen.add((dst, h["kind"]))
            out.append(Edge(r.author_id, dst, h["kind"], r.id, r.published_at))
    return out


def _chunks(items: list, n: int = 1000) -> Iterable[list]:  # type: ignore[type-arg]
    for i in range(0, len(items), n):
        yield items[i : i + n]


def save_edges(conn: Connection, topic_id: int, es: list[Edge]) -> None:
    conn.execute(delete(edges).where(edges.c.topic_id == topic_id))
    if es:
        conn.execute(
            insert(edges),
            [
                {
                    "topic_id": topic_id,
                    "src_author_id": e.src,
                    "dst_author_id": e.dst,
                    "kind": e.kind,
                    "post_id": e.post_id,
                    "occurred_at": e.occurred_at,
                }
                for e in es
            ],
        )


def build_graph(es: Iterable[Edge], nodes: Iterable[int] = ()) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    weights: dict[tuple[int, int], float] = defaultdict(float)
    for e in es:
        weights[(e.src, e.dst)] += EDGE_WEIGHTS.get(e.kind, 1)
    for (s, d), w in weights.items():
        g.add_edge(s, d, weight=w)
    return g


def pagerank(g: nx.DiGraph, alpha: float = 0.85) -> dict[int, float]:
    if g.number_of_nodes() == 0:
        return {}
    return dict(nx.pagerank(g, alpha=alpha, weight="weight"))
