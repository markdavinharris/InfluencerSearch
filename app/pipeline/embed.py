"""Embeddings for on-topic posts (used for the "similar posts" panel)."""

from sqlalchemy import Connection, and_, bindparam, exists, select, update

from app.config import get_settings
from app.llm import EmbedClient
from app.log import get_logger
from app.models import post_topics, posts

log = get_logger(__name__)

MAX_EMBED_CHARS = 6000


def pending_post_ids(conn: Connection, limit: int) -> list[tuple[int, str]]:
    min_conf = get_settings().on_topic_min_confidence
    on_topic = exists().where(
        and_(
            post_topics.c.post_id == posts.c.id,
            post_topics.c.on_topic,
            post_topics.c.confidence >= min_conf,
        )
    )
    q = (
        select(posts.c.id, posts.c.text)
        .where(posts.c.embedding.is_(None), on_topic, posts.c.text != "")
        .order_by(posts.c.id)
        .limit(limit)
    )
    return [(r.id, r.text) for r in conn.execute(q)]


def embed_pending(
    conn: Connection, embedder: EmbedClient, batch_size: int = 100, max_posts: int = 20000
) -> int:
    done = 0
    while done < max_posts:
        batch = pending_post_ids(conn, min(batch_size, max_posts - done))
        if not batch:
            break
        vectors = embedder.embed([t[:MAX_EMBED_CHARS] for _, t in batch])
        conn.execute(
            update(posts).where(posts.c.id == bindparam("pid")).values(embedding=bindparam("vec")),
            [{"pid": pid, "vec": vec} for (pid, _), vec in zip(batch, vectors, strict=True)],
        )
        conn.commit()
        done += len(batch)
    log.info("embed_done", embedded=done, model=embedder.model)
    return done
