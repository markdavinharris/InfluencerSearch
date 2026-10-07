"""Read-side SQL shared by the API routers."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, text

from app.schemas import AuthorRef

AUTHOR_COLS = """a.id AS a_id, a.platform AS a_platform, a.handle AS a_handle,
    a.display_name AS a_display_name, a.avatar_url AS a_avatar_url, a.url AS a_url,
    a.followers AS a_followers, a.person_id AS a_person_id"""


def author_ref(r: Any) -> AuthorRef:
    return AuthorRef(
        id=r.a_id,
        platform=r.a_platform,
        handle=r.a_handle,
        display_name=r.a_display_name,
        avatar_url=r.a_avatar_url,
        url=r.a_url,
        followers=r.a_followers,
        person_id=r.a_person_id,
    )


def latest_as_of(conn: Connection, topic_id: int, on_or_before: date | None = None) -> date | None:
    return conn.execute(
        text(
            "SELECT max(as_of) FROM author_scores WHERE topic_id = :t "
            "AND (CAST(:d AS date) IS NULL OR as_of <= :d)"
        ),
        {"t": topic_id, "d": on_or_before},
    ).scalar()


def scores_at(conn: Connection, topic_id: int, as_of: date) -> dict[int, float]:
    rows = conn.execute(
        text("SELECT author_id, composite FROM author_scores WHERE topic_id = :t AND as_of = :d"),
        {"t": topic_id, "d": as_of},
    )
    return {r.author_id: r.composite for r in rows}


def comparison_date(conn: Connection, topic_id: int, as_of: date, days: int) -> date | None:
    """Latest snapshot on or before as_of - days."""
    return latest_as_of(conn, topic_id, as_of - timedelta(days=days))
