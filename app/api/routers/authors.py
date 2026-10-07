from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, text

from app.api.deps import get_conn
from app.api.queries import AUTHOR_COLS, author_ref
from app.config import get_settings
from app.schemas import (
    AuthorDetail,
    AuthorPost,
    AuthorRef,
    AuthorTopicScore,
    EdgeSummary,
    PersonRef,
    ScoreHistory,
    ScorePoint,
)

router = APIRouter(prefix="/authors", tags=["authors"])
Conn = Annotated[Connection, Depends(get_conn)]


def _edge_summaries(
    conn: Connection, author_id: int, direction: str, topic_id: int | None
) -> list[EdgeSummary]:
    me, other = (
        ("dst_author_id", "src_author_id")
        if direction == "in"
        else ("src_author_id", "dst_author_id")
    )
    rows = conn.execute(
        text(f"""
        SELECT {AUTHOR_COLS}, e.kind, count(*) AS n
        FROM edges e JOIN authors a ON a.id = e.{other}
        WHERE e.{me} = :id AND (CAST(:t AS int) IS NULL OR e.topic_id = :t)
        GROUP BY a.id, e.kind
        """),
        {"id": author_id, "t": topic_id},
    ).all()
    agg: dict[int, EdgeSummary] = {}
    for r in rows:
        s = agg.setdefault(r.a_id, EdgeSummary(author=author_ref(r), count=0, kinds={}))
        s.count += r.n
        s.kinds[r.kind] = s.kinds.get(r.kind, 0) + r.n
    return sorted(agg.values(), key=lambda s: -s.count)[:25]


@router.get("", response_model=list[AuthorRef])
def search_authors(
    conn: Conn,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[AuthorRef]:
    """Find accounts by handle or display name (for the merge-into-person control)."""
    like = "%" + q.lower().lstrip("@").replace("%", "").replace("_", r"\_") + "%"
    rows = conn.execute(
        text(f"""SELECT {AUTHOR_COLS} FROM authors a
                 WHERE lower(a.handle) LIKE :q OR lower(a.display_name) LIKE :q
                 ORDER BY a.followers DESC NULLS LAST LIMIT :lim"""),
        {"q": like, "lim": limit},
    ).all()
    return [author_ref(r) for r in rows]


@router.get("/{author_id}", response_model=AuthorDetail)
def author_detail(
    author_id: int,
    conn: Conn,
    topic: str | None = None,
    posts_limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> AuthorDetail:
    a = conn.execute(
        text(
            f"SELECT {AUTHOR_COLS}, a.bio, a.following, a.first_seen, a.last_seen "
            "FROM authors a WHERE a.id = :id"
        ),
        {"id": author_id},
    ).first()
    if a is None:
        raise HTTPException(404, "author not found")
    topic_id = None
    if topic:
        topic_id = conn.execute(
            text("SELECT id FROM topics WHERE slug = :s"), {"s": topic}
        ).scalar()
        if topic_id is None:
            raise HTTPException(404, f"unknown topic {topic!r}")

    person = None
    if a.a_person_id:
        p = conn.execute(
            text("SELECT id, display_name FROM persons WHERE id = :id"), {"id": a.a_person_id}
        ).one()
        accounts = conn.execute(
            text(f"SELECT {AUTHOR_COLS} FROM authors a WHERE a.person_id = :p ORDER BY a.id"),
            {"p": p.id},
        ).all()
        person = PersonRef(
            id=p.id, display_name=p.display_name, accounts=[author_ref(x) for x in accounts]
        )

    scores = [
        AuthorTopicScore(
            topic_slug=r.slug,
            topic_name=r.name,
            as_of=r.as_of,
            rank=r.rank,
            composite=r.composite,
            centrality=r.centrality,
            engagement=r.engagement,
            reach=r.reach,
            on_topic_posts=r.on_topic_posts,
        )
        for r in conn.execute(
            text("""
            SELECT DISTINCT ON (s.topic_id) t.slug, t.name, s.as_of, s.rank, s.composite,
                   s.centrality, s.engagement, s.reach, s.on_topic_posts
            FROM author_scores s JOIN topics t ON t.id = s.topic_id
            WHERE s.author_id = :id ORDER BY s.topic_id, s.as_of DESC
            """),
            {"id": author_id},
        )
    ]
    scores.sort(key=lambda s: s.rank)

    recent = [
        AuthorPost(
            id=r.id,
            topic_slug=r.slug,
            text=r.text,
            url=r.url,
            published_at=r.published_at,
            kind=r.kind,
            likes=r.likes,
            reposts=r.reposts,
            replies=r.replies,
            quotes=r.quotes,
            stance=r.stance,
            summary=r.summary,
            confidence=r.confidence,
        )
        for r in conn.execute(
            text("""
            SELECT p.id, t.slug, p.text, p.url, p.published_at, p.kind, p.likes, p.reposts,
                   p.replies, p.quotes, pt.stance, pt.summary, pt.confidence
            FROM posts p
            JOIN post_topics pt ON pt.post_id = p.id
            JOIN topics t ON t.id = pt.topic_id
            WHERE p.author_id = :id AND pt.on_topic AND pt.confidence >= :mc
              AND (CAST(:t AS int) IS NULL OR pt.topic_id = :t)
            ORDER BY p.published_at DESC LIMIT :lim
            """),
            {
                "id": author_id,
                "mc": get_settings().on_topic_min_confidence,
                "t": topic_id,
                "lim": posts_limit,
            },
        )
    ]
    return AuthorDetail(
        author=author_ref(a),
        bio=a.bio,
        following=a.following,
        first_seen=a.first_seen,
        last_seen=a.last_seen,
        person=person,
        scores=scores,
        recent_posts=recent,
        cited_by=_edge_summaries(conn, author_id, "in", topic_id),
        cites=_edge_summaries(conn, author_id, "out", topic_id),
    )


@router.get("/{author_id}/score-history", response_model=ScoreHistory)
def score_history(
    author_id: int,
    conn: Conn,
    topic: str,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> ScoreHistory:
    rows = conn.execute(
        text("""
        SELECT s.as_of, s.composite, s.centrality, s.engagement, s.reach, s.rank
        FROM author_scores s JOIN topics t ON t.id = s.topic_id
        WHERE s.author_id = :id AND t.slug = :slug
          AND s.as_of > (SELECT max(as_of) FROM author_scores) - CAST(:days AS int)
        ORDER BY s.as_of
        """),
        {"id": author_id, "slug": topic, "days": days},
    ).all()
    return ScoreHistory(
        author_id=author_id, topic=topic, points=[ScorePoint(**r._mapping) for r in rows]
    )
