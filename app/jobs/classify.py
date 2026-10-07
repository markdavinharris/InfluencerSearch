"""Classify pending posts for all active topics.

    python -m app.jobs.classify [--topic SLUG ...] [--limit N]

Exits non-zero (after saving finished work) if the LLM daily token cap is hit.
"""

import argparse
import sys

from app.db import get_engine
from app.llm import LLM, TokenCapExceeded
from app.log import get_logger, setup_logging
from app.pipeline.classify import Classifier, classify_topic
from app.topics import active_topics

log = get_logger(__name__)


def run_classify(topic_slugs: list[str] | None = None, limit: int | None = None) -> bool:
    """Returns False if stopped by the token cap."""
    engine = get_engine()
    classifier = Classifier(LLM(engine=engine))
    with engine.connect() as conn:
        topics = [t for t in active_topics(conn) if not topic_slugs or t.slug in topic_slugs]
        for topic in topics:
            try:
                stats = classify_topic(conn, topic, classifier, limit=limit)
            except TokenCapExceeded as exc:
                log.error("classify_stopped_token_cap", topic=topic.slug, error=str(exc))
                return False
            log.info("classify_topic_done", topic=topic.slug, **stats)
    return True


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--topic", action="append")
    p.add_argument("--limit", type=int, help="max candidates per topic (for testing)")
    a = p.parse_args()
    sys.exit(0 if run_classify(a.topic, a.limit) else 2)


if __name__ == "__main__":
    main()
