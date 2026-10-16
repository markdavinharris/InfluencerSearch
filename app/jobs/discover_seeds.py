"""Find seed-account candidates for topics, for review on the topic's Seeds page.

    python -m app.jobs.discover_seeds [--topic SLUG ...] [--source NAME ...]
                                      [--min-followers N] [--x-reads N] [--dry-run]

Sources: existing (free, from collected data), bluesky, x (spends X budget, default 100
reads per topic), youtube (one 100-unit channel search per topic), podcasts. Each source runs
on its own and is logged in collector_runs as "seeds:<source>", so a failure in one never
stops the others. See app/discovery.py for how candidates are scored.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, update

from app.collectors import collector_class
from app.config import get_settings
from app.db import get_engine
from app.discovery import (
    SOURCES,
    CandidatePool,
    config_seed_keys,
    from_bluesky,
    from_existing,
    from_podcasts,
    from_x,
    from_youtube,
    save_candidates,
)
from app.log import get_logger, setup_logging
from app.models import collector_runs
from app.topics import active_topics, get_topic

log = get_logger(__name__)

DEFAULT_MIN_FOLLOWERS = 500
DEFAULT_X_READS = 100


def run_discovery(
    topic_slugs: list[str] | None = None,
    sources: list[str] | None = None,
    min_followers: int = DEFAULT_MIN_FOLLOWERS,
    x_reads: int = DEFAULT_X_READS,
    dry_run: bool = False,
    record_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    engine = get_engine()
    sources = sources or list(SOURCES)
    today = datetime.now(UTC).date()
    summary: dict[str, dict[str, Any]] = {}

    with engine.connect() as conn:
        all_topics = active_topics(conn)
        if topic_slugs:
            # allow discovery for an inactive topic when asked for by name
            topics = [t for s in topic_slugs if (t := get_topic(conn, s)) is not None]
        else:
            topics = all_topics
        conn.commit()

        clients: dict[str, Any] = {}
        for src in sources:
            if src == "existing":
                continue
            kwargs = {"n_topics": len(all_topics)} if src in ("x", "youtube") else {}
            client = collector_class(src)(
                settings=settings, engine=engine, record_dir=record_dir, **kwargs
            )
            if client.is_configured():
                clients[src] = client
            else:
                log.warning("discover_source_not_configured", source=src)

        for topic in topics:
            pool = CandidatePool(topic)
            per_source: dict[str, Any] = {}
            for src in sources:
                if src != "existing" and src not in clients:
                    continue
                run_id = None
                if not dry_run:
                    run_id = conn.execute(
                        insert(collector_runs)
                        .values(topic_id=topic.id, platform=f"seeds:{src}")
                        .returning(collector_runs.c.id)
                    ).scalar_one()
                    conn.commit()
                error = None
                found = 0
                try:
                    if src == "existing":
                        found = from_existing(conn, topic, pool, settings)
                    elif src == "bluesky":
                        found = from_bluesky(clients[src], topic, pool)
                    elif src == "x":
                        found = from_x(clients[src], topic, pool, x_reads, today)
                    elif src == "youtube":
                        found = from_youtube(
                            clients[src], engine, topic, pool, settings, today, len(all_topics)
                        )
                    elif src == "podcasts":
                        found = from_podcasts(clients[src], topic, pool)
                except Exception as exc:
                    conn.rollback()
                    error = f"{type(exc).__name__}: {exc}"[:2000]
                    log.exception("discover_source_failed", source=src, topic=topic.slug)
                per_source[src] = error or found
                if run_id is not None:
                    conn.execute(
                        update(collector_runs)
                        .where(collector_runs.c.id == run_id)
                        .values(finished_at=func.now(), fetched=found, error=error)
                    )
                    conn.commit()

            cands = pool.results(min_followers, config_seed_keys(topic))
            if dry_run:
                for c in cands[:50]:
                    print(
                        json.dumps(
                            {
                                "platform": c.platform,
                                "seed": c.seed_handle,
                                "name": c.display_name,
                                "followers": c.followers,
                                "posts": c.matched_posts,
                                "bio_match": c.bio_match,
                                "keywords": sorted(c.matched_keywords),
                                "score": round(c.score, 2),
                            },
                            ensure_ascii=False,
                        )
                    )
            else:
                save_candidates(conn, topic.id, cands)
                conn.commit()
            summary[topic.slug] = {"candidates": len(cands), "sources": per_source}
            log.info(
                "discover_topic_done", topic=topic.slug, candidates=len(cands), sources=per_source
            )
    return summary


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--topic", action="append")
    p.add_argument("--source", action="append", choices=list(SOURCES))
    p.add_argument("--min-followers", type=int, default=DEFAULT_MIN_FOLLOWERS)
    p.add_argument(
        "--x-reads",
        type=int,
        default=DEFAULT_X_READS,
        help="max X posts read per topic (counts against the X budget)",
    )
    p.add_argument("--dry-run", action="store_true", help="print top candidates, don't save")
    p.add_argument("--record", type=Path, help="save raw API responses to this directory")
    a = p.parse_args()
    summary = run_discovery(a.topic, a.source, a.min_followers, a.x_reads, a.dry_run, a.record)
    failed = any(isinstance(v, str) for t in summary.values() for v in t["sources"].values())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
