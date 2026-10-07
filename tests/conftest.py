import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import Engine, text

from app import config as app_config
from app import db as app_db

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="session")
def _test_db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get(
        "TEST_DATABASE_URL"
    )
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture(scope="session")
def _migrated(_test_db_url: str) -> Engine:
    engine = app_db.make_engine(_test_db_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"db_url={_test_db_url}"]})()  # type: ignore[assignment]
    command.upgrade(cfg, "head")
    return engine


@pytest.fixture
def db(_migrated: Engine, _test_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A freshly truncated test database; app code's get_engine() points at it."""
    with _migrated.begin() as conn:
        tables = (
            conn.execute(
                text(
                    "select tablename from pg_tables where schemaname='public' "
                    "and tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    monkeypatch.setenv("DATABASE_URL", _test_db_url)
    app_config.get_settings.cache_clear()
    app_db.get_engine.cache_clear()
    yield _migrated
    app_config.get_settings.cache_clear()
    app_db.get_engine.cache_clear()


@pytest.fixture
def topic_factory(db: Engine):  # type: ignore[no-untyped-def]
    from app.jobs.sync_topics import upsert_topic
    from app.schemas import TopicConfig
    from app.topics import get_topic

    def make(slug: str = "pa-energy", **overrides: Any):  # type: ignore[no-untyped-def]
        data: dict[str, Any] = {
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "description": "Energy policy in Pennsylvania.",
            "keywords": ["RGGI", "PJM capacity"],
            "exclude_keywords": ["Pennsylvania Avenue"],
            "subreddits": ["pittsburgh"],
        } | overrides
        cfg = TopicConfig.model_validate(data)
        with db.begin() as conn:
            upsert_topic(conn, cfg)
            topic = get_topic(conn, slug)
        assert topic is not None
        return topic

    return make
