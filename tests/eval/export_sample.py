"""Export a random sample of collected posts for a topic as JSONL, ready to hand-label.

python -m tests.eval.export_sample --topic pa-energy -n 50 > labels.jsonl
"""

import argparse
import json

from sqlalchemy import func, select

from app.db import get_engine
from app.models import post_sources, posts, topics


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--topic", required=True)
    p.add_argument("-n", type=int, default=50)
    a = p.parse_args()
    with get_engine().connect() as conn:
        q = (
            select(posts.c.id, posts.c.platform, posts.c.kind, posts.c.text, posts.c.url)
            .select_from(
                post_sources.join(posts, posts.c.id == post_sources.c.post_id).join(
                    topics, topics.c.id == post_sources.c.topic_id
                )
            )
            .where(topics.c.slug == a.topic, func.length(posts.c.text) >= 15)
            .order_by(func.random())
            .limit(a.n)
        )
        for r in conn.execute(q):
            print(
                json.dumps(
                    {
                        "topic": a.topic,
                        "post_id": r.id,
                        "platform": r.platform,
                        "kind": r.kind,
                        "url": r.url,
                        "text": r.text,
                        "on_topic": None,
                        "note": "",
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
