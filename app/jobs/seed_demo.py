"""Fill a LOCAL database with synthetic demo data for UI development.

    uv run python -m app.jobs.seed_demo [--force]

Creates fake authors/posts/classifications for every active topic over the last 30 days
and scores each day, so sparklines, deltas and Rising have history. Refuses to run against
a non-local DATABASE_URL unless --force. Demo rows are marked with platform ids starting
'demo-'.
"""

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select

from app.collectors.base import Author, EdgeHint, EdgeKind, Post, store_post
from app.config import get_settings
from app.db import get_engine
from app.models import post_topics, posts
from app.pipeline.score import score_topic
from app.topics import active_topics

NAMES = [
    ("x", "statehousereporter", "Statehouse Reporter", 41000),
    ("x", "paenergywatch", "PA Energy Watch", 12000),
    ("x", "gridanalyst", "Grid Analyst", 8800),
    ("bluesky", "cleanairpa.bsky.social", "Clean Air PA", 5200),
    ("bluesky", "utilitynerd.bsky.social", "Utility Nerd", 2100),
    ("x", "senatorsmith", "Sen. Pat Smith", 30500),
    ("reddit", "pghhomeowner", "pghhomeowner", 40),
    ("reddit", "taxnerd412", "taxnerd412", 310),
    ("x", "chamberpa", "PA Chamber", 18000),
    ("x", "localeditor", "Local Editor", 6400),
    ("bluesky", "labor.bsky.social", "Building Trades", 3300),
    ("x", "randomvoter1", "Random Voter", 90),
    ("x", "randomvoter2", "Another Voter", 150),
    ("reddit", "steelcityowner", "steelcityowner", 12),
]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    url = get_settings().database_url
    if not a.force and "localhost" not in url and "127.0.0.1" not in url:
        sys.exit(f"refusing to seed non-local database {url!r} (use --force)")
    rng = random.Random(42)
    engine = get_engine()
    today = datetime.now(UTC).date()
    with engine.connect() as conn:
        for topic in active_topics(conn):
            people = [
                Author(
                    platform=pl,
                    platform_id=f"demo-{h}",
                    handle=h,
                    display_name=n,
                    followers=f,
                    bio=f"Demo account for {topic.name}.",
                    url=f"https://example.com/{h}",
                )
                for pl, h, n, f in NAMES
            ]
            hubs = people[:4]
            n = 0
            for day in range(40, -1, -1):
                ts = datetime.now(UTC) - timedelta(days=day, hours=rng.randint(0, 20))
                for _ in range(rng.randint(4, 9)):
                    author = rng.choice(people)
                    hints = []
                    if rng.random() < 0.6:
                        target = rng.choice(hubs if rng.random() < 0.75 else people)
                        if target is not author and target.platform == author.platform:
                            kind: EdgeKind = rng.choice(["quote", "repost", "reply", "mention"])
                            hints.append(
                                EdgeHint(
                                    kind, target.platform, author_platform_id=target.platform_id
                                )
                            )
                    n += 1
                    on = rng.random() < 0.8
                    post = Post(
                        platform=author.platform,
                        platform_id=f"demo-{topic.slug}-{n}",
                        author=author,
                        text=f"[demo] {topic.name}: point #{n} from {author.display_name}.",
                        url="https://example.com/post",
                        published_at=ts,
                        likes=rng.randint(0, 300) if author in hubs else rng.randint(0, 20),
                        reposts=rng.randint(0, 40),
                        replies=rng.randint(0, 15),
                        quotes=rng.randint(0, 5),
                        edge_hints=hints,
                        kind="quote" if hints and hints[0].kind == "quote" else "post",
                    )
                    store_post(conn, post, topic.id)
                    pid = conn.execute(
                        select(posts.c.id).where(
                            posts.c.platform == post.platform,
                            posts.c.platform_id == post.platform_id,
                        )
                    ).scalar_one()
                    conn.execute(
                        insert(post_topics).values(
                            post_id=pid,
                            topic_id=topic.id,
                            on_topic=on,
                            confidence=rng.uniform(0.65, 0.98) if on else 0.9,
                            stance=rng.choice(["support", "oppose", "neutral", "unclear"])
                            if on
                            else None,
                            summary=f"Demo summary of point #{n}." if on else None,
                            model="demo",
                        )
                    )
            conn.commit()
            for back in range(29, -1, -1):
                score_topic(conn, topic, today - timedelta(days=back))
            print(f"seeded {topic.slug}: {n} posts")


if __name__ == "__main__":
    main()
