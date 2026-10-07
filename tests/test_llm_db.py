from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select

from app.collectors.base import Author, Post, store_post
from app.config import Settings
from app.llm import LLM, TokenCapExceeded, log_call, tokens_used_today
from app.models import llm_calls, post_topics, posts
from app.pipeline.embed import embed_pending
from tests.fakes import FakeEmbedder


@pytest.mark.db
def test_token_cap(db) -> None:  # type: ignore[no-untyped-def]
    log_call(db, "classify", "m", 600, 300)
    with db.begin() as conn:  # yesterday's spend doesn't count
        conn.execute(
            insert(llm_calls).values(
                purpose="classify",
                model="m",
                input_tokens=10_000,
                output_tokens=0,
                created_at=datetime.now(UTC) - timedelta(days=1, hours=1),
            )
        )
    assert tokens_used_today(db) == 900
    llm = LLM(settings=Settings(llm_daily_token_cap=1000), engine=db)
    llm.check_cap()
    log_call(db, "classify", "m", 100, 0)
    with pytest.raises(TokenCapExceeded):
        llm.check_cap()


@pytest.mark.db
def test_embed_only_on_topic_confident_posts(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    with db.connect() as conn:
        ids = []
        for i, (on, conf) in enumerate([(True, 0.9), (True, 0.3), (False, 0.9)]):
            store_post(
                conn,
                Post(
                    platform="x",
                    platform_id=str(i),
                    author=Author(platform="x", platform_id="a"),
                    text=f"post {i} about PJM",
                    published_at=datetime.now(UTC),
                ),
                topic.id,
            )
            pid = conn.execute(select(posts.c.id).where(posts.c.platform_id == str(i))).scalar_one()
            ids.append(pid)
            conn.execute(
                insert(post_topics).values(
                    post_id=pid, topic_id=topic.id, on_topic=on, confidence=conf
                )
            )
        conn.commit()
        assert embed_pending(conn, FakeEmbedder()) == 1
        assert embed_pending(conn, FakeEmbedder()) == 0
        n = conn.execute(select(func.count()).where(posts.c.embedding.is_not(None))).scalar()
        assert n == 1
