from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, text

from app.api.deps import get_conn
from app.collectors.x_budget import XBudget
from app.config import get_settings
from app.db import get_engine
from app.llm import tokens_used_today
from app.schemas import CollectorRunRow, LlmSpendRow, Stats, XBudgetStatus

router = APIRouter(tags=["stats"])
Conn = Annotated[Connection, Depends(get_conn)]


@router.get("/stats", response_model=Stats)
def stats(
    conn: Conn,
    runs: Annotated[int, Query(ge=1, le=500)] = 100,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> Stats:
    settings = get_settings()
    collector_runs = [
        CollectorRunRow(**r._mapping)
        for r in conn.execute(
            text("""SELECT r.id, t.slug AS topic_slug, r.platform, r.started_at, r.finished_at,
                           r.fetched, r.inserted, r.error
                    FROM collector_runs r LEFT JOIN topics t ON t.id = r.topic_id
                    ORDER BY r.started_at DESC LIMIT :n"""),
            {"n": runs},
        )
    ]
    spend = [
        LlmSpendRow(**r._mapping)
        for r in conn.execute(
            text("""SELECT (created_at AT TIME ZONE 'UTC')::date AS day, model, purpose,
                           count(*) AS calls, sum(input_tokens) AS input_tokens,
                           sum(output_tokens) AS output_tokens
                    FROM llm_calls WHERE created_at >= now() - make_interval(days => :d)
                    GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3"""),
            {"d": days},
        )
    ]
    n_topics = conn.execute(text("SELECT count(*) FROM topics WHERE active")).scalar_one()
    engine = get_engine()
    st = XBudget(engine, settings.x_monthly_post_budget, n_topics).status(datetime.now(UTC).date())
    totals: dict[str, int] = {
        k: int(v)
        for k, v in conn.execute(
            text("""
            SELECT 'posts', count(*) FROM posts UNION ALL
            SELECT 'authors', count(*) FROM authors UNION ALL
            SELECT 'classified', count(*) FROM post_topics UNION ALL
            SELECT 'on_topic', count(*) FROM post_topics WHERE on_topic UNION ALL
            SELECT 'embedded', count(*) FROM posts WHERE embedding IS NOT NULL UNION ALL
            SELECT 'edges', count(*) FROM edges
        """)
        )
    }
    return Stats(
        collector_runs=collector_runs,
        llm_spend=spend,
        llm_tokens_today=tokens_used_today(engine),
        llm_daily_token_cap=settings.llm_daily_token_cap,
        x_budget=XBudgetStatus(
            month=st.month,
            monthly_budget=st.monthly_budget,
            posts_read=st.posts_read,
            remaining=st.remaining,
            daily_allocation_per_topic=st.daily_allocation_per_topic,
            active_topics=n_topics,
        ),
        totals=totals,
    )
