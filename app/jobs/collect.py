"""Run collectors for active topics.

    python -m app.jobs.collect [--topic SLUG ...] [--platform NAME ...] [--dry-run]
                               [--since-days N] [--record DIR]

`since` defaults to the start of the last successful run for that topic+platform minus one
day of overlap (re-collected posts are de-duplicated), or the topic's window on first run.
--record saves every raw API response as JSON (use it to build tests/fixtures).
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Connection, func, select

from app.collectors import REGISTRY, collector_class
from app.collectors.base import RunResult
from app.config import get_settings
from app.db import get_engine
from app.log import get_logger, setup_logging
from app.models import collector_runs
from app.schemas import Topic
from app.topics import active_topics

log = get_logger(__name__)


def default_since(conn: Connection, topic: Topic, platform: str) -> datetime:
    window_start = datetime.now(UTC) - timedelta(days=topic.window_days)
    last_ok = conn.execute(
        select(func.max(collector_runs.c.started_at)).where(
            collector_runs.c.topic_id == topic.id,
            collector_runs.c.platform == platform,
            collector_runs.c.error.is_(None),
            collector_runs.c.finished_at.is_not(None),
        )
    ).scalar()
    if last_ok is None:
        return window_start
    return max(window_start, last_ok - timedelta(days=1))


def run_collect(
    topic_slugs: list[str] | None = None,
    platforms: list[str] | None = None,
    dry_run: bool = False,
    since_days: int | None = None,
    record_dir: Path | None = None,
) -> list[RunResult]:
    settings = get_settings()
    engine = get_engine()
    names = platforms or [p for p in REGISTRY if p in settings.enabled_collector_set]
    results: list[RunResult] = []
    with engine.connect() as conn:
        topics = active_topics(conn)
        conn.commit()
        if topic_slugs:
            topics = [t for t in topics if t.slug in topic_slugs]
        for name in names:
            try:
                cls = collector_class(name)
                kwargs = {"n_topics": len(active_topics(conn))} if name == "x" else {}
                collector = cls(settings=settings, engine=engine, record_dir=record_dir, **kwargs)
            except Exception as exc:
                log.exception("collector_init_failed", platform=name)
                results.append(
                    RunResult(platform=name, topic_slug="*", error=f"{type(exc).__name__}: {exc}")
                )
                continue
            if not collector.is_configured():
                log.warning("collector_not_configured", platform=name)
                continue
            for topic in topics:
                since = (
                    datetime.now(UTC) - timedelta(days=since_days)
                    if since_days
                    else default_since(conn, topic, collector.platform)
                )
                conn.commit()
                results.append(collector.run(conn, topic, since, dry_run=dry_run))
    return results


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--topic", action="append", help="topic slug (repeatable)")
    p.add_argument("--platform", action="append", choices=list(REGISTRY))
    p.add_argument("--dry-run", action="store_true", help="print normalized posts, no writes")
    p.add_argument("--since-days", type=int)
    p.add_argument("--record", type=Path, help="save raw API responses to this directory")
    a = p.parse_args()
    results = run_collect(a.topic, a.platform, a.dry_run, a.since_days, a.record)
    failed = [r for r in results if r.error]
    log.info(
        "collect_done",
        runs=len(results),
        failed=len(failed),
        inserted=sum(r.inserted for r in results),
    )
    sys.exit(1 if results and len(failed) == len(results) else 0)


if __name__ == "__main__":
    main()
