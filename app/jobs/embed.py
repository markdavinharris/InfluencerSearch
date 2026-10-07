"""Embed on-topic posts that don't have an embedding yet.

python -m app.jobs.embed [--max N]
"""

import argparse

from app.db import get_engine
from app.llm import Embedder
from app.log import setup_logging
from app.pipeline.embed import embed_pending


def run_embed(max_posts: int = 20000) -> int:
    engine = get_engine()
    with engine.connect() as conn:
        return embed_pending(conn, Embedder(engine=engine), max_posts=max_posts)


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max", type=int, default=20000)
    run_embed(p.parse_args().max)


if __name__ == "__main__":
    main()
