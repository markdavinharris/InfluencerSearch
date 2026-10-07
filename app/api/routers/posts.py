from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, text

from app.api.deps import get_conn
from app.api.queries import AUTHOR_COLS, author_ref
from app.config import get_settings
from app.schemas import SimilarPost

router = APIRouter(prefix="/posts", tags=["posts"])
Conn = Annotated[Connection, Depends(get_conn)]


@router.get("/{post_id}/similar", response_model=list[SimilarPost])
def similar(
    post_id: int,
    conn: Conn,
    topic: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SimilarPost]:
    """Nearest on-topic posts by cosine distance, within `topic` (default: the topics this
    post is on)."""
    src = conn.execute(
        text("SELECT embedding IS NOT NULL AS has FROM posts WHERE id = :id"), {"id": post_id}
    ).first()
    if src is None:
        raise HTTPException(404, "post not found")
    if not src.has:
        return []
    rows = conn.execute(
        text(f"""
        WITH src AS (SELECT embedding FROM posts WHERE id = :id),
        scope AS (
            SELECT t.id FROM topics t WHERE t.slug = :slug
            UNION
            SELECT pt.topic_id FROM post_topics pt
            WHERE CAST(:slug AS text) IS NULL AND pt.post_id = :id AND pt.on_topic
        )
        SELECT p.id, p.text, p.url, p.published_at, pt.summary,
               1 - (p.embedding <=> (SELECT embedding FROM src)) AS similarity, {AUTHOR_COLS}
        FROM posts p
        JOIN authors a ON a.id = p.author_id
        JOIN LATERAL (
            SELECT summary FROM post_topics x
            WHERE x.post_id = p.id AND x.topic_id IN (SELECT id FROM scope)
              AND x.on_topic AND x.confidence >= :mc
            LIMIT 1
        ) pt ON true
        WHERE p.id <> :id AND p.embedding IS NOT NULL
        ORDER BY p.embedding <=> (SELECT embedding FROM src)
        LIMIT :lim
        """),
        {"id": post_id, "slug": topic, "mc": get_settings().on_topic_min_confidence, "lim": limit},
    ).all()
    return [
        SimilarPost(
            id=r.id,
            author=author_ref(r),
            text=r.text,
            url=r.url,
            published_at=r.published_at,
            summary=r.summary,
            similarity=float(r.similarity),
        )
        for r in rows
    ]
