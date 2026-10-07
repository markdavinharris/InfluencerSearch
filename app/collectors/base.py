"""Collector base class, normalized dataclasses, persistence, retries and rate limiting.

A collector only has to implement `collect(topic, since) -> Iterator[Post]`. `Collector.run`
wraps it: it records a `collector_runs` row, upserts authors and posts idempotently, records
which topic surfaced each post (`post_sources`), and stores any exception in
`collector_runs.error` instead of raising.
"""

import json
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from sqlalchemy import Connection, Engine, func, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings, get_settings
from app.log import get_logger
from app.models import api_quota, authors, collector_runs, post_sources, posts
from app.schemas import Topic

log = get_logger(__name__)

EdgeKind = Literal["quote", "repost", "reply", "mention", "link"]
PostKind = Literal["post", "reply", "repost", "quote", "article", "video", "episode"]


# ---------------------------------------------------------------- normalized data


@dataclass
class Author:
    platform: str
    platform_id: str
    handle: str | None = None
    display_name: str | None = None
    bio: str | None = None
    url: str | None = None
    avatar_url: str | None = None
    followers: int | None = None
    following: int | None = None
    raw: dict[str, Any] | None = None


@dataclass
class EdgeHint:
    """What a post points at. The target author is identified by whichever of
    `author_platform_id` / `handle` / `post_platform_id` the platform gives us; the graph
    builder resolves them (in that order) against the authors and posts tables."""

    kind: EdgeKind
    platform: str
    author_platform_id: str | None = None
    handle: str | None = None
    post_platform_id: str | None = None


@dataclass
class Post:
    platform: str
    platform_id: str
    author: Author
    text: str
    published_at: datetime
    url: str | None = None
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0
    views: int | None = None
    kind: PostKind = "post"
    parent_platform_id: str | None = None
    edge_hints: list[EdgeHint] = field(default_factory=list)
    raw: dict[str, Any] | None = None
    # Authors referenced by this post (quoted/replied-to/mentioned) whose profile we got for
    # free in the same response. Upserted so edges to them resolve.
    related_authors: list[Author] = field(default_factory=list)

    def to_json(self) -> str:
        d = asdict(self)
        d.pop("raw", None)
        d["author"].pop("raw", None)
        for a in d["related_authors"]:
            a.pop("raw", None)
        return json.dumps(d, default=str, ensure_ascii=False)


@dataclass
class RunResult:
    platform: str
    topic_slug: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None


# ---------------------------------------------------------------- helpers


class CollectorNotConfigured(RuntimeError):
    pass


class BudgetExhausted(Exception):
    """Raised inside a collector to stop early because a quota/budget is used up.
    Not an error: the run is recorded as successful."""


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def with_retries[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Retry transient HTTP failures (network errors, 429, 5xx) with exponential backoff."""
    return retry(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(multiplier=2, max=30),
        reraise=True,
    )(fn)


class RateLimiter:
    """Minimum interval between calls (per collector instance)."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delay = self._last + self.interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def parse_dt(value: str | int | float | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC)
    s = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


_MENTION_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_]{1,15})\b")


def text_mentions(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _MENTION_RE.finditer(text)))


def quota_used(conn: Connection, platform: str, scope: str, day: date) -> int:
    row = conn.execute(
        api_quota.select().where(
            api_quota.c.platform == platform, api_quota.c.scope == scope, api_quota.c.day == day
        )
    ).first()
    return int(row.units) if row else 0


def quota_add(conn: Connection, platform: str, scope: str, day: date, units: int) -> None:
    stmt = pg_insert(api_quota).values(platform=platform, scope=scope, day=day, units=units)
    stmt = stmt.on_conflict_do_update(
        index_elements=[api_quota.c.platform, api_quota.c.scope, api_quota.c.day],
        set_={"units": api_quota.c.units + units},
    )
    conn.execute(stmt)


# ---------------------------------------------------------------- persistence


_AUTHOR_UPDATABLE = (
    "handle",
    "display_name",
    "bio",
    "url",
    "avatar_url",
    "followers",
    "following",
    "raw",
)


def upsert_author(conn: Connection, a: Author) -> int:
    values = {
        "platform": a.platform,
        "platform_id": a.platform_id,
        "handle": a.handle,
        "display_name": a.display_name,
        "bio": a.bio,
        "url": a.url,
        "avatar_url": a.avatar_url,
        "followers": a.followers,
        "following": a.following,
        "raw": a.raw,
    }
    stmt = pg_insert(authors).values(**values)
    # Don't overwrite known values with NULLs from a sparser response.
    set_ = {k: func.coalesce(stmt.excluded[k], authors.c[k]) for k in _AUTHOR_UPDATABLE} | {
        "last_seen": func.now()
    }
    upsert = stmt.on_conflict_do_update(
        index_elements=[authors.c.platform, authors.c.platform_id], set_=set_
    ).returning(authors.c.id)
    return int(conn.execute(upsert).scalar_one())


def upsert_post(conn: Connection, p: Post, author_id: int) -> tuple[int, bool]:
    """Insert or refresh engagement counts. Returns (post id, inserted?)."""
    values = {
        "platform": p.platform,
        "platform_id": p.platform_id,
        "author_id": author_id,
        "text": p.text or "",
        "url": p.url,
        "published_at": p.published_at,
        "likes": p.likes or 0,
        "reposts": p.reposts or 0,
        "replies": p.replies or 0,
        "quotes": p.quotes or 0,
        "views": p.views,
        "kind": p.kind,
        "parent_platform_id": p.parent_platform_id,
        "edge_hints": [asdict(h) for h in p.edge_hints],
        "raw": p.raw,
    }
    stmt = pg_insert(posts).values(**values)
    upsert = stmt.on_conflict_do_update(
        index_elements=[posts.c.platform, posts.c.platform_id],
        set_={
            "likes": func.greatest(stmt.excluded.likes, posts.c.likes),
            "reposts": func.greatest(stmt.excluded.reposts, posts.c.reposts),
            "replies": func.greatest(stmt.excluded.replies, posts.c.replies),
            "quotes": func.greatest(stmt.excluded.quotes, posts.c.quotes),
            "views": func.coalesce(stmt.excluded.views, posts.c.views),
        },
    ).returning(posts.c.id, literal_column("(xmax = 0)").label("inserted"))
    # xmax = 0 is Postgres' tell that the row was freshly inserted rather than updated.
    row = conn.execute(upsert).one()
    return int(row.id), bool(row.inserted)


def store_post(conn: Connection, p: Post, topic_id: int) -> bool:
    for ra in p.related_authors:
        upsert_author(conn, ra)
    author_id = upsert_author(conn, p.author)
    post_id, inserted = upsert_post(conn, p, author_id)
    conn.execute(
        pg_insert(post_sources).values(post_id=post_id, topic_id=topic_id).on_conflict_do_nothing()
    )
    return inserted


# ---------------------------------------------------------------- the ABC


class Collector(ABC):
    platform: str

    def __init__(
        self,
        settings: Settings | None = None,
        engine: Engine | None = None,
        record_dir: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # For budget/quota bookkeeping, written in short transactions of their own so a
        # failed run can't roll back the record of API calls already made. Optional so tests
        # can run collectors without a database.
        self.engine = engine
        self.record_dir = record_dir
        self._record_seq = 0

    def is_configured(self) -> bool:
        return True

    @abstractmethod
    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]: ...

    def record(self, name: str, payload: Any) -> None:
        """Save a raw API response (for building test fixtures) when --record is on."""
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self._record_seq += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]
        path = self.record_dir / f"{self.platform}_{self._record_seq:03d}_{safe}.json"
        path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False))

    def run(
        self,
        conn: Connection,
        topic: Topic,
        since: datetime,
        dry_run: bool = False,
        printer: Callable[[str], None] = print,
    ) -> RunResult:
        result = RunResult(platform=self.platform, topic_slug=topic.slug)
        run_id: int | None = None
        if not dry_run:
            run_id = conn.execute(
                insert(collector_runs)
                .values(topic_id=topic.id, platform=self.platform)
                .returning(collector_runs.c.id)
            ).scalar_one()
            conn.commit()
        try:
            for post in self.collect(topic, since):
                result.fetched += 1
                if dry_run:
                    printer(post.to_json())
                    continue
                if store_post(conn, post, topic.id):
                    result.inserted += 1
                if result.fetched % 200 == 0:
                    conn.commit()
        except BudgetExhausted as exc:
            log.info(
                "collector_budget_stop", platform=self.platform, topic=topic.slug, reason=str(exc)
            )
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"[:2000]
            log.exception("collector_failed", platform=self.platform, topic=topic.slug)
            if not dry_run:
                # Keep what was stored before the failure; if the failure was the DB itself
                # the transaction is aborted and commit fails, so roll back instead.
                try:
                    conn.commit()
                except Exception:
                    conn.rollback()
        if not dry_run:
            conn.commit()
        if not dry_run and run_id is not None:
            conn.execute(
                update(collector_runs)
                .where(collector_runs.c.id == run_id)
                .values(
                    finished_at=func.now(),
                    fetched=result.fetched,
                    inserted=result.inserted,
                    error=result.error,
                )
            )
            conn.commit()
        log.info(
            "collector_done",
            platform=self.platform,
            topic=topic.slug,
            fetched=result.fetched,
            inserted=result.inserted,
            error=result.error,
            dry_run=dry_run,
        )
        return result


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


def strip_html(html: str | None) -> str:
    import html as html_lib

    if not html:
        return ""
    text = html_lib.unescape(_TAG_RE.sub(" ", html))
    return "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()).strip()


def keyword_matcher(keywords: list[str]) -> Callable[[str], bool]:
    """Case-insensitive 'contains any keyword' (whole-word for single short tokens like RGGI)."""
    parts = []
    for kw in keywords:
        k = re.escape(kw.strip().strip('"').lower())
        parts.append(rf"\b{k}\b")
    rx = re.compile("|".join(parts), re.IGNORECASE) if parts else None
    return lambda text: bool(rx and rx.search(text or ""))


def domain_of(url: str | None) -> str | None:
    from urllib.parse import urlparse

    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def quota_scope_allowance(
    conn: Connection, platform: str, scope: str, day: date, daily_cap: int, n_scopes: int
) -> int:
    """Remaining units today for one scope (topic) when a daily cap is split evenly."""
    per_scope = max(1, daily_cap // max(1, n_scopes))
    total = conn.execute(
        select(func.coalesce(func.sum(api_quota.c.units), 0)).where(
            api_quota.c.platform == platform, api_quota.c.day == day
        )
    ).scalar_one()
    mine = quota_used(conn, platform, scope, day)
    return max(0, min(per_scope - mine, daily_cap - int(total)))


def entry_published(entry: Any) -> datetime | None:
    """Publication time of a feedparser entry (its *_parsed fields are UTC struct_time)."""
    import calendar

    for key in ("published_parsed", "updated_parsed"):
        if entry.get(key):
            return datetime.fromtimestamp(calendar.timegm(entry[key]), tz=UTC)
    return None
