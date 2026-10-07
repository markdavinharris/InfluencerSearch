"""News collector — GDELT DOC 2.0 article search plus configured RSS/Atom feeds.

Author = outlet, keyed by domain (for both GDELT and RSS, so an outlet found both ways is one
author and `link` edges to it resolve by handle). Article text is pulled with trafilatura for
up to MAX_FULLTEXT articles per run (classification then sees real text, not just a title);
outbound links in the article body to other outlets become `link` edge hints.

RSS feeds come from NEWS_RSS_FEEDS (all topics) and each topic's `rss_feeds`. Entries are
kept only if they mention a topic keyword, since feeds aren't topic-filtered.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx

from app.collectors.base import (
    Author,
    Collector,
    EdgeHint,
    Post,
    RateLimiter,
    domain_of,
    entry_published,
    keyword_matcher,
    strip_html,
    with_retries,
)
from app.log import get_logger
from app.schemas import Topic

log = get_logger(__name__)

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_FULLTEXT = 100
MAX_TEXT = 6000
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
NOT_OUTLETS = {
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "linkedin.com",
    "t.co",
    "bit.ly",
    "google.com",
    "apple.com",
    "reddit.com",
    "tiktok.com",
    "bsky.app",
    "threads.net",
    "mailto",
}


def build_gdelt_query(keywords: list[str], exclude: list[str]) -> str:
    # GDELT: phrases in quotes, OR-groups in parens, terms must be >= 3 chars.
    terms = [f'"{k}"' if " " in k else k for k in keywords if len(k.strip()) >= 3]
    q = terms[0] if len(terms) == 1 else f"({' OR '.join(terms)})"
    q += "".join(f' -"{x}"' if " " in x else f" -{x}" for x in exclude)
    return q + " sourcelang:english"


def outlet_author(domain: str, name: str | None = None) -> Author:
    return Author(
        platform="news",
        platform_id=domain,
        handle=domain,
        display_name=name or domain,
        url=f"https://{domain}",
    )


def link_hints(html: str, base_url: str, own_domain: str) -> list[EdgeHint]:
    domains: dict[str, None] = {}
    for href in HREF_RE.findall(html or ""):
        d = domain_of(urljoin(base_url, href))
        if d and d != own_domain and not any(d == n or d.endswith("." + n) for n in NOT_OUTLETS):
            domains[d] = None
    return [EdgeHint(kind="link", platform="news", handle=d) for d in domains]


def normalize_gdelt(resp: dict[str, Any]) -> list[Post]:
    out = []
    for a in resp.get("articles") or []:
        domain = (a.get("domain") or domain_of(a.get("url")) or "").lower()
        if not domain or not a.get("url"):
            continue
        seen = a.get("seendate")  # 20261005T140000Z
        published = (
            datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            if seen
            else datetime.now(UTC)
        )
        out.append(
            Post(
                platform="news",
                platform_id=a["url"],
                author=outlet_author(domain),
                text=a.get("title") or "",
                url=a["url"],
                published_at=published,
                kind="article",
                raw={
                    k: a.get(k)
                    for k in (
                        "title",
                        "domain",
                        "language",
                        "sourcecountry",
                        "seendate",
                        "socialimage",
                    )
                },
            )
        )
    return out


def normalize_feed(parsed: Any, feed_url: str) -> list[Post]:
    feed = parsed.get("feed", {})
    site = feed.get("link") or feed_url
    domain = domain_of(site) or domain_of(feed_url) or feed_url
    author = outlet_author(domain, feed.get("title"))
    author.bio = strip_html(feed.get("subtitle"))[:500] or None
    out = []
    for e in parsed.get("entries", []):
        link = e.get("link")
        if not link:
            continue
        published = entry_published(e)
        body = strip_html(e.get("summary") or "")
        out.append(
            Post(
                platform="news",
                platform_id=link,
                author=author,
                text=(e.get("title") or "") + ("\n\n" + body if body else ""),
                url=link,
                published_at=published or datetime.now(UTC),
                kind="article",
                raw={"feed": feed_url, "title": e.get("title"), "author": e.get("author")},
            )
        )
    return out


class NewsCollector(Collector):
    platform = "news"

    def __init__(self, *args: Any, http: httpx.Client | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http = http
        self.limiter = RateLimiter(per_second=1)
        self.fulltext_budget = MAX_FULLTEXT

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (influence-mapper)"},
            )
        return self._http

    @with_retries
    def _gdelt(self, query: str, since: datetime) -> dict[str, Any]:
        self.limiter.wait()
        r = self.http.get(
            GDELT,
            params={
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": 250,
                "sort": "datedesc",
                "startdatetime": since.astimezone(UTC).strftime("%Y%m%d%H%M%S"),
            },
        )
        r.raise_for_status()
        if not r.text.strip().startswith("{"):
            # GDELT reports query problems as plain text with a 200
            raise ValueError(f"GDELT rejected query: {r.text[:200]}")
        data: dict[str, Any] = r.json()
        self.record("gdelt", data)
        return data

    @with_retries
    def _fetch(self, url: str) -> httpx.Response:
        self.limiter.wait()
        r = self.http.get(url)
        r.raise_for_status()
        return r

    def _enrich_fulltext(self, post: Post) -> None:
        if self.fulltext_budget <= 0 or not post.url:
            return
        self.fulltext_budget -= 1
        try:
            html = self._fetch(post.url).text
        except httpx.HTTPError as exc:  # paywalls, 403s: keep the headline
            log.info("news_fetch_failed", url=post.url, error=str(exc)[:200])
            return
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text:
            title = post.text.split("\n", 1)[0]
            post.text = (title + "\n\n" + text)[:MAX_TEXT]
        post.edge_hints = link_hints(html, post.url, post.author.platform_id)

    def _feeds(self, topic: Topic) -> list[str]:
        return list(dict.fromkeys(self.settings.rss_feed_list + topic.config.rss_feeds))

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        matches = keyword_matcher(topic.config.keywords)
        excludes = [x.lower() for x in topic.config.exclude_keywords]
        seen: set[str] = set()

        def keep(p: Post) -> bool:
            if p.platform_id in seen or p.published_at < since:
                return False
            if any(x in p.text.lower() for x in excludes):
                return False
            seen.add(p.platform_id)
            return True

        for feed_url in self._feeds(topic):
            try:
                resp = self._fetch(feed_url)
            except httpx.HTTPError as exc:
                # One dead feed shouldn't fail the run; it's logged for the status page.
                log.warning("rss_feed_failed", feed=feed_url, error=str(exc)[:200])
                continue
            for post in normalize_feed(feedparser.parse(resp.content), feed_url):
                if matches(post.text) and keep(post):
                    self._enrich_fulltext(post)
                    yield post

        query = build_gdelt_query(topic.config.keywords, topic.config.exclude_keywords)
        for post in normalize_gdelt(self._gdelt(query, since)):
            if keep(post):
                self._enrich_fulltext(post)
                yield post
