"""Idempotency of the collector persistence layer (Phase 1 acceptance criterion)."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.collectors.base import Collector, Post
from app.collectors.x import normalize_response
from app.models import authors, collector_runs, post_sources, posts
from app.schemas import Topic
from tests.conftest import load_fixture


class FixtureCollector(Collector):
    platform = "x"

    def __init__(self, fail_after: int | None = None) -> None:
        super().__init__()
        self.fail_after = fail_after

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        for i, p in enumerate(normalize_response(load_fixture("x_search_recent.json"))):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("API exploded")
            yield p


def _count(conn, table) -> int:  # type: ignore[no-untyped-def]
    return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


@pytest.mark.db
def test_collect_twice_inserts_nothing_new(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    since = datetime(2026, 10, 1, tzinfo=UTC)
    with db.connect() as conn:
        r1 = FixtureCollector().run(conn, topic, since)
        r2 = FixtureCollector().run(conn, topic, since)
        assert (r1.fetched, r1.inserted, r1.error) == (4, 4, None)
        assert (r2.fetched, r2.inserted, r2.error) == (4, 0, None)
        assert _count(conn, posts) == 4
        # 4 posting authors + PAPUC + GovShapiro stubs from mentions
        assert _count(conn, authors) == 6
        assert _count(conn, post_sources) == 4
        runs = conn.execute(select(collector_runs).order_by(collector_runs.c.id)).all()
        assert [(r.fetched, r.inserted) for r in runs] == [(4, 4), (4, 0)]
        assert all(r.finished_at is not None for r in runs)
        hints = conn.execute(
            select(posts.c.edge_hints).where(posts.c.platform_id == "1844000000000000004")
        ).scalar_one()
        assert {h["kind"] for h in hints} == {"reply", "mention"}


@pytest.mark.db
def test_collector_error_is_recorded_not_raised(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    with db.connect() as conn:
        r = FixtureCollector(fail_after=2).run(conn, topic, datetime(2026, 10, 1, tzinfo=UTC))
        assert r.error == "RuntimeError: API exploded"
        assert _count(conn, posts) == 2  # what was stored before the failure is kept
        run = conn.execute(select(collector_runs)).one()
        assert run.error == "RuntimeError: API exploded" and run.inserted == 2


@pytest.mark.db
def test_dry_run_writes_nothing(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    lines: list[str] = []
    with db.connect() as conn:
        r = FixtureCollector().run(
            conn, topic, datetime(2026, 10, 1, tzinfo=UTC), dry_run=True, printer=lines.append
        )
        assert r.fetched == 4 and len(lines) == 4
        assert _count(conn, posts) == 0 and _count(conn, collector_runs) == 0
