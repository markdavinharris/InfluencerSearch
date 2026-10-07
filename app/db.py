"""Engine and connection helpers. Plain SQLAlchemy Core over psycopg 3."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings


def normalize_url(url: str) -> str:
    """Accept postgres:// / postgresql:// URLs (as Supabase and Railway hand out) and force
    the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    # Supabase's pooled connection string goes through pgbouncer in transaction mode:
    # no server-side prepared statements, and let pgbouncer do the pooling.
    if ":6543/" in url or "pooler.supabase.com" in url:
        return create_engine(
            url,
            poolclass=NullPool,
            connect_args={"prepare_threshold": None},
        )
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


@contextmanager
def connect() -> Iterator[Connection]:
    """A connection inside a transaction that commits on success, rolls back on error."""
    with get_engine().begin() as conn:
        yield conn
