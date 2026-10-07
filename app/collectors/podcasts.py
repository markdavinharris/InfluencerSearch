"""Podcast collector — Podcast Index search by term, then the show's RSS feed.

For each topic keyword, `search/byterm` finds shows; each show's feed is parsed with
feedparser and recent episodes (published since `since`) whose title or description
mention a topic keyword are kept. Seed shows (`podcast:<feed url>` in seed_accounts) are
always read and all their recent episodes kept. Classification uses title + description
only (no transcription in v1). Author = show, keyed by Podcast Index feed id (or feed URL
for seeds not found in the index).
"""

import hashlib
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import feedparser
import httpx

from app.collectors.base import (
    Author,
    Collector,
    CollectorNotConfigured,
    Post,
    RateLimiter,
    entry_published,
    keyword_matcher,
    strip_html,
    with_retries,
)
from app.log import get_logger
from app.schemas import Topic

log = get_logger(__name__)

API = "https://api.podcastindex.org/api/1.0"
MAX_SHOWS_PER_KEYWORD = 15
MAX_EPISODE_TEXT = 3000


def auth_headers(key: str, secret: str, now: int | None = None) -> dict[str, str]:
    ts = str(now if now is not None else int(time.time()))
    return {
        "X-Auth-Key": key,
        "X-Auth-Date": ts,
        "Authorization": hashlib.sha1((key + secret + ts).encode()).hexdigest(),
        "User-Agent": "coldspark-influence-mapper/0.1",
    }


def show_author(feed: dict[str, Any]) -> Author:
    return Author(
        platform="podcast",
        platform_id=str(feed.get("id") or feed["url"]),
        handle=feed.get("title"),
        display_name=feed.get("title"),
        bio=strip_html(feed.get("description"))[:1000] or None,
        url=feed.get("link") or feed.get("url"),
        avatar_url=feed.get("image") or feed.get("artwork"),
        raw={
            k: feed.get(k) for k in ("id", "url", "author", "ownerName", "language", "categories")
        },
    )


def normalize_episodes(parsed: Any, author: Author) -> list[Post]:
    out = []
    for e in parsed.get("entries", []):
        guid = e.get("id") or e.get("link") or (e.get("title") or "")
        if not guid:
            continue
        published = entry_published(e)
        if published is None:
            continue
        desc = strip_html(e.get("summary") or e.get("description") or "")
        out.append(
            Post(
                platform="podcast",
                platform_id=f"{author.platform_id}:{guid}",
                author=author,
                text=((e.get("title") or "") + ("\n\n" + desc if desc else ""))[:MAX_EPISODE_TEXT],
                url=e.get("link") or author.url,
                published_at=published,
                kind="episode",
                raw={"title": e.get("title"), "guid": guid},
            )
        )
    return out


class PodcastCollector(Collector):
    platform = "podcast"

    def __init__(self, *args: Any, http: httpx.Client | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http = http
        self.limiter = RateLimiter(per_second=2)

    def is_configured(self) -> bool:
        return bool(self.settings.podcastindex_key and self.settings.podcastindex_secret)

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=30, follow_redirects=True)
        return self._http

    @with_retries
    def _api(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured():
            raise CollectorNotConfigured("PODCASTINDEX_KEY / PODCASTINDEX_SECRET not set")
        self.limiter.wait()
        r = self.http.get(
            API + path,
            params=params,
            headers=auth_headers(self.settings.podcastindex_key, self.settings.podcastindex_secret),
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        self.record(path.strip("/").replace("/", "_"), data)
        return data

    @with_retries
    def _feed(self, url: str) -> Any:
        self.limiter.wait()
        r = self.http.get(url, headers={"User-Agent": "coldspark-influence-mapper/0.1"})
        r.raise_for_status()
        return feedparser.parse(r.content)

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        matches = keyword_matcher(topic.config.keywords)
        excludes = [x.lower() for x in topic.config.exclude_keywords]
        shows: dict[str, tuple[dict[str, Any], bool]] = {}  # feed url -> (feed, is_seed)
        for url in topic.config.seeds_for("podcast"):
            found = self._api("/podcasts/byfeedurl", {"url": url}).get("feed") or {}
            shows[url] = ({**found, "url": url} if found else {"url": url, "title": url}, True)
        for kw in topic.config.keywords:
            for feed in self._api(
                "/search/byterm", {"q": kw, "max": MAX_SHOWS_PER_KEYWORD, "clean": "true"}
            ).get("feeds", []):
                if feed.get("url") and feed["url"] not in shows:
                    shows[feed["url"]] = (feed, False)

        for url, (feed, is_seed) in shows.items():
            try:
                parsed = self._feed(url)
            except httpx.HTTPError as exc:
                log.warning("podcast_feed_failed", feed=url, error=str(exc)[:200])
                continue
            author = show_author(feed)
            for ep in normalize_episodes(parsed, author):
                if ep.published_at < since or any(x in ep.text.lower() for x in excludes):
                    continue
                if is_seed or matches(ep.text):
                    yield ep
