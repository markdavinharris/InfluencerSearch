"""Bluesky collector.

Talks XRPC directly over httpx (rather than the `atproto` SDK) so recorded fixtures are the
exact wire JSON. `app.bsky.feed.searchPosts` per keyword plus `getAuthorFeed` for seeds;
follower counts come from a batched `app.bsky.actor.getProfiles`.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import (
    Author,
    Collector,
    CollectorNotConfigured,
    EdgeHint,
    Post,
    RateLimiter,
    parse_dt,
    with_retries,
)
from app.log import get_logger
from app.schemas import Topic

log = get_logger(__name__)

PDS = "https://bsky.social/xrpc"
APPVIEW = "https://api.bsky.app/xrpc"
MAX_PAGES_PER_KEYWORD = 10


def did_from_uri(uri: str) -> str | None:
    # at://did:plc:abc/app.bsky.feed.post/xyz
    if not uri.startswith("at://"):
        return None
    return uri[5:].split("/", 1)[0]


def web_url(handle: str | None, uri: str) -> str:
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle or did_from_uri(uri)}/post/{rkey}"


def normalize_author(a: dict[str, Any]) -> Author:
    return Author(
        platform="bluesky",
        platform_id=a["did"],
        handle=a.get("handle"),
        display_name=a.get("displayName") or a.get("handle"),
        bio=a.get("description"),
        url=f"https://bsky.app/profile/{a.get('handle') or a['did']}",
        avatar_url=a.get("avatar"),
        followers=a.get("followersCount"),
        following=a.get("followsCount"),
        raw={k: v for k, v in a.items() if k not in ("viewer", "labels")},
    )


def _embedded_record(embed: dict[str, Any] | None) -> dict[str, Any] | None:
    """The quoted post view inside a post's embed (record or recordWithMedia)."""
    if not embed:
        return None
    t = embed.get("$type", "")
    if t.startswith("app.bsky.embed.recordWithMedia"):
        return (embed.get("record") or {}).get("record")
    if t.startswith("app.bsky.embed.record"):
        return embed.get("record")
    return None


def normalize_post_view(
    pv: dict[str, Any], reason: dict[str, Any] | None = None, reposter: dict[str, Any] | None = None
) -> Post:
    """Normalize an app.bsky.feed.defs#postView. If `reason` is a repost reason (from an
    author feed), produce a repost by `reposter` pointing at the original."""
    rec = pv.get("record") or {}
    author_raw = pv["author"]
    if reason and reason.get("$type", "").endswith("reasonRepost") and reposter:
        original = normalize_author(author_raw)
        by = normalize_author(reason.get("by") or reposter)
        return Post(
            platform="bluesky",
            platform_id=f"repost:{by.platform_id}:{pv['uri']}",
            author=by,
            text=rec.get("text", ""),
            url=web_url(author_raw.get("handle"), pv["uri"]),
            published_at=parse_dt(reason.get("indexedAt")) or datetime.now(UTC),
            kind="repost",
            parent_platform_id=pv["uri"],
            edge_hints=[
                EdgeHint(
                    kind="repost",
                    platform="bluesky",
                    author_platform_id=original.platform_id,
                    post_platform_id=pv["uri"],
                )
            ],
            related_authors=[original],
            raw={"reason": reason, "uri": pv["uri"]},
        )

    hints: list[EdgeHint] = []
    related: list[Author] = []
    kind = "post"
    parent: str | None = None

    reply = rec.get("reply")
    if reply and reply.get("parent", {}).get("uri"):
        parent = reply["parent"]["uri"]
        kind = "reply"
        hints.append(
            EdgeHint(
                kind="reply",
                platform="bluesky",
                author_platform_id=did_from_uri(parent),
                post_platform_id=parent,
            )
        )

    quoted = _embedded_record(pv.get("embed"))
    if quoted and quoted.get("uri") and quoted.get("$type", "").endswith("viewRecord"):
        if kind == "post":
            kind = "quote"
        parent = parent or quoted["uri"]
        qa = quoted.get("author")
        hints.append(
            EdgeHint(
                kind="quote",
                platform="bluesky",
                author_platform_id=(qa or {}).get("did") or did_from_uri(quoted["uri"]),
                post_platform_id=quoted["uri"],
            )
        )
        if qa:
            related.append(normalize_author(qa))

    for facet in rec.get("facets") or []:
        for feat in facet.get("features") or []:
            ft = feat.get("$type", "")
            if ft.endswith("#mention") and feat.get("did"):
                hints.append(
                    EdgeHint(kind="mention", platform="bluesky", author_platform_id=feat["did"])
                )

    author = normalize_author(author_raw)
    return Post(
        platform="bluesky",
        platform_id=pv["uri"],
        author=author,
        text=rec.get("text", ""),
        url=web_url(author.handle, pv["uri"]),
        published_at=parse_dt(rec.get("createdAt"))
        or parse_dt(pv.get("indexedAt"))
        or datetime.now(UTC),
        likes=pv.get("likeCount", 0),
        reposts=pv.get("repostCount", 0),
        replies=pv.get("replyCount", 0),
        quotes=pv.get("quoteCount", 0),
        kind=kind,  # type: ignore[arg-type]
        parent_platform_id=parent,
        edge_hints=hints,
        related_authors=[a for a in related if a.platform_id != author.platform_id],
        raw={k: v for k, v in pv.items() if k not in ("viewer", "labels")},
    )


class BlueskyCollector(Collector):
    platform = "bluesky"

    def __init__(self, *args: Any, http: httpx.Client | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http = http
        self._token: str | None = None
        self.limiter = RateLimiter(per_second=5)

    def is_configured(self) -> bool:
        return bool(self.settings.bluesky_handle and self.settings.bluesky_app_password)

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=30)
        return self._http

    def _login(self) -> None:
        if not self.is_configured():
            raise CollectorNotConfigured("BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set")
        r = self.http.post(
            f"{PDS}/com.atproto.server.createSession",
            json={
                "identifier": self.settings.bluesky_handle,
                "password": self.settings.bluesky_app_password,
            },
        )
        r.raise_for_status()
        self._token = r.json()["accessJwt"]

    @with_retries
    def _get(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._token is None:
            self._login()
        self.limiter.wait()
        r = self.http.get(
            f"{PDS}/{method}", params=params, headers={"Authorization": f"Bearer {self._token}"}
        )
        if r.status_code == 400 and "ExpiredToken" in r.text:
            self._login()
            r = self.http.get(
                f"{PDS}/{method}", params=params, headers={"Authorization": f"Bearer {self._token}"}
            )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        self.record(method.rsplit(".", 1)[-1], data)
        return data

    def _enrich(self, batch: list[Post]) -> None:
        """Fill follower counts via getProfiles (25 actors per call)."""
        dids = list({a.platform_id for p in batch for a in [p.author, *p.related_authors]})
        profiles: dict[str, Author] = {}
        for i in range(0, len(dids), 25):
            resp = self._get("app.bsky.actor.getProfiles", {"actors": dids[i : i + 25]})
            for prof in resp.get("profiles", []):
                profiles[prof["did"]] = normalize_author(prof)
        for p in batch:
            if p.author.platform_id in profiles:
                p.author = profiles[p.author.platform_id]
            p.related_authors = [profiles.get(a.platform_id, a) for a in p.related_authors]

    def _search(self, keyword: str, since: datetime) -> Iterator[list[Post]]:
        cursor = None
        for _ in range(MAX_PAGES_PER_KEYWORD):
            params: dict[str, Any] = {
                "q": keyword,
                "limit": 100,
                "sort": "latest",
                "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if cursor:
                params["cursor"] = cursor
            resp = self._get("app.bsky.feed.searchPosts", params)
            yield [normalize_post_view(pv) for pv in resp.get("posts", [])]
            cursor = resp.get("cursor")
            if not cursor or not resp.get("posts"):
                return

    def _author_feed(self, actor: str, since: datetime) -> Iterator[list[Post]]:
        cursor = None
        for _ in range(MAX_PAGES_PER_KEYWORD):
            params: dict[str, Any] = {"actor": actor, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = self._get("app.bsky.feed.getAuthorFeed", params)
            page = []
            oldest: datetime | None = None
            for item in resp.get("feed", []):
                post = normalize_post_view(
                    item["post"], item.get("reason"), reposter={"did": actor, "handle": actor}
                )
                oldest = post.published_at if oldest is None else min(oldest, post.published_at)
                if post.published_at >= since:
                    page.append(post)
            yield page
            cursor = resp.get("cursor")
            if not cursor or oldest is None or oldest < since:
                return

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        seen: set[str] = set()
        excludes = [x.lower() for x in topic.config.exclude_keywords]
        sources: list[Iterator[list[Post]]] = [
            self._author_feed(h, since) for h in topic.config.seeds_for("bluesky")
        ] + [self._search(k, since) for k in topic.config.keywords]
        for pages in sources:
            for page in pages:
                fresh = [
                    p
                    for p in page
                    if p.platform_id not in seen and not any(x in p.text.lower() for x in excludes)
                ]
                seen.update(p.platform_id for p in fresh)
                if fresh:
                    self._enrich(fresh)
                yield from fresh
