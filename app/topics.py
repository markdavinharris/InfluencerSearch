"""Loading topic definitions from YAML and from the DB."""

from pathlib import Path

import yaml
from sqlalchemy import Connection, select

from app.models import seed_candidates, topics
from app.schemas import Topic, TopicConfig

TOPICS_DIR = Path(__file__).resolve().parent.parent / "topics"


def load_topic_files(directory: Path = TOPICS_DIR) -> list[TopicConfig]:
    """Every *.yaml in the topics dir except example.yaml."""
    configs = []
    for path in sorted(directory.glob("*.y*ml")):
        if path.stem == "example":
            continue
        data = yaml.safe_load(path.read_text())
        cfg = TopicConfig.model_validate(data)
        configs.append(cfg)
    slugs = [c.slug for c in configs]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    if dupes:
        raise ValueError(f"duplicate topic slugs: {sorted(dupes)}")
    return configs


def _row_to_topic(row) -> Topic:  # type: ignore[no-untyped-def]
    return Topic(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        window_days=row.window_days,
        active=row.active,
        config=TopicConfig.model_validate(row.config),
    )


def _with_accepted_seeds(conn: Connection, topic: Topic) -> Topic:
    """Seeds accepted in the UI (seed_candidates) are added to the YAML's seed_accounts."""
    accepted = [
        f"{r.platform}:{r.seed_handle}"
        for r in conn.execute(
            select(seed_candidates.c.platform, seed_candidates.c.seed_handle)
            .where(seed_candidates.c.topic_id == topic.id, seed_candidates.c.status == "accepted")
            .order_by(seed_candidates.c.id)
        )
    ]
    if accepted:
        merged = list(dict.fromkeys([*topic.config.seed_accounts, *accepted]))
        topic.config = topic.config.model_copy(update={"seed_accounts": merged})
    return topic


def active_topics(conn: Connection) -> list[Topic]:
    rows = conn.execute(select(topics).where(topics.c.active).order_by(topics.c.id)).all()
    return [_with_accepted_seeds(conn, _row_to_topic(r)) for r in rows]


def get_topic(conn: Connection, slug: str) -> Topic | None:
    row = conn.execute(select(topics).where(topics.c.slug == slug)).first()
    return _with_accepted_seeds(conn, _row_to_topic(row)) if row else None
