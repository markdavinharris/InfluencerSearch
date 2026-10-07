"""Build edges, run PageRank and write today's author_scores snapshot for active topics.

python -m app.jobs.score [--topic SLUG ...] [--as-of YYYY-MM-DD]
"""

import argparse
from datetime import UTC, date, datetime

from app.db import get_engine
from app.log import get_logger, setup_logging
from app.pipeline.score import score_topic
from app.topics import active_topics

log = get_logger(__name__)


def run_score(topic_slugs: list[str] | None = None, as_of: date | None = None) -> None:
    as_of = as_of or datetime.now(UTC).date()
    with get_engine().connect() as conn:
        topics = [t for t in active_topics(conn) if not topic_slugs or t.slug in topic_slugs]
        for topic in topics:
            score_topic(conn, topic, as_of)


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--topic", action="append")
    p.add_argument("--as-of", type=date.fromisoformat)
    a = p.parse_args()
    run_score(a.topic, a.as_of)


if __name__ == "__main__":
    main()
