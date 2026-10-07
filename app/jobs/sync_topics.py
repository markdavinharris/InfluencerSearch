"""Load topics/*.yaml into the topics table.

    python -m app.jobs.sync_topics [--deactivate-missing]

Topics present in the DB but no longer in topics/ are left alone unless
--deactivate-missing is passed (they're never deleted: scores reference them).
"""

import argparse

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from app.db import connect
from app.log import get_logger, setup_logging
from app.models import topics
from app.topics import load_topic_files

log = get_logger(__name__)


def sync_topics(deactivate_missing: bool = False) -> int:
    configs = load_topic_files()
    with connect() as conn:
        for cfg in configs:
            values = {
                "slug": cfg.slug,
                "name": cfg.name,
                "description": cfg.description.strip(),
                "config": cfg.model_dump(),
                "window_days": cfg.window_days,
                "active": cfg.active,
            }
            stmt = insert(topics).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[topics.c.slug],
                set_={
                    **{k: stmt.excluded[k] for k in values if k != "slug"},
                    "updated_at": func.now(),
                },
            )
            conn.execute(stmt)
            log.info("topic_synced", slug=cfg.slug, active=cfg.active)
        if deactivate_missing:
            slugs = [c.slug for c in configs]
            res = conn.execute(
                update(topics).where(topics.c.slug.not_in(slugs)).values(active=False)
            )
            log.info("topics_deactivated", count=res.rowcount)
    return len(configs)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deactivate-missing", action="store_true")
    args = parser.parse_args()
    n = sync_topics(args.deactivate_missing)
    log.info("sync_topics_done", topics=n)


if __name__ == "__main__":
    main()
