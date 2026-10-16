"""Seed-account discovery: find accounts worth following for a topic, for a human to pick.

Sources (each optional; a missing credential just skips it):

- existing   accounts already in the DB with on-topic posts for the topic (free)
- bluesky    searchPosts per keyword (who is posting about it) + searchActors per keyword
             (who describes themselves with it); follower counts via getProfiles
- x          ONE recent-search page per topic, paid from the X read budget (capped by
             --x-reads, recorded under api_quota scope "discover:<slug>")
- youtube    ONE channel search per topic (100 quota units, same daily cap as collection)
- podcasts   Podcast Index search by keyword (shows; no follower counts exist)

Candidates are scored, filtered by minimum followers, and upserted into `seed_candidates`.
Re-running refreshes stats but never changes a human's accept/reject decision.

Score = log10(1 + followers) * (1 + log2(1 + matching posts)) * 1.5 if the bio mentions a
keyword * (1 + 0.25 per extra keyword matched). Followers set the scale, topical activity
multiplies it; it's only an ordering for review, not a ranking of influence.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.collectors.base import Author, Post, keyword_matcher, quota_add, quota_scope_allowance
from app.config import Settings
from app.log import get_logger
from app.models import authors, post_topics, posts, seed_candidates
from app.schemas import SeedAccount, Topic

log = get_logger(__name__)

SOURCES = ("existing", "bluesky", "x", "youtube", "podcasts")
SAMPLE_CHARS = 400


@dataclass
class Candidate:
    platform: str
    platform_id: str
    seed_handle: str
    display_name: str | None = None
    bio: str | None = None
    url: str | None = None
    avatar_url: str | None = None
    followers: int | None = None
    matched_posts: int = 0
    matched_keywords: set[str] = field(default_factory=set)
    bio_match: bool = False
    sample_text: str | None = None
    sample_url: str | None = None
    sources: set[str] = field(default_factory=set)

    @property
    def score(self) -> float:
        s = math.log10(1 + (self.followers or 0)) * (1 + math.log2(1 + self.matched_posts))
        if self.bio_match:
            s *= 1.5
        return s * (1 + 0.25 * max(0, len(self.matched_keywords) - 1))

    def merge(self, other: "Candidate") -> None:
        for f in ("display_name", "bio", "url", "avatar_url", "sample_text", "sample_url"):
            if getattr(self, f) is None:
                setattr(self, f, getattr(other, f))
        if other.followers is not None:
            self.followers = max(self.followers or 0, other.followers)
        self.matched_posts += other.matched_posts
        self.matched_keywords |= other.matched_keywords
        self.bio_match |= other.bio_match
        self.sources |= other.sources


def seed_handle_for(author: Author) -> str:
    """The identifier collectors accept after "platform:" in seed_accounts."""
    if author.platform == "youtube":
        return author.handle or author.platform_id  # "@handle", or the UC… channel id
    if author.platform == "podcast":
        return (author.raw or {}).get("url") or author.platform_id  # feed URL
    return author.handle or author.platform_id


def candidate_from_author(a: Author, source: str) -> Candidate:
    return Candidate(
        platform=a.platform,
        platform_id=a.platform_id,
        seed_handle=seed_handle_for(a),
        display_name=a.display_name or a.handle,
        bio=a.bio,
        url=a.url,
        avatar_url=a.avatar_url,
        followers=a.followers,
        sources={source},
    )


class CandidatePool:
    """Accumulates candidates across keywords and sources, keyed by (platform, platform_id)."""

    def __init__(self, topic: Topic) -> None:
        self.topic = topic
        self.items: dict[tuple[str, str], Candidate] = {}
        self._bio_matches = keyword_matcher(topic.config.keywords)
        self._excludes = [x.lower() for x in topic.config.exclude_keywords]

    def _add(self, c: Candidate) -> Candidate:
        c.bio_match = c.bio_match or self._bio_matches(c.bio or "")
        key = (c.platform, c.platform_id)
        if key in self.items:
            self.items[key].merge(c)
        else:
            self.items[key] = c
        return self.items[key]

    def add_posts(self, keywords: str | set[str], found: Iterable[Post], source: str) -> None:
        """Authors of posts a keyword search returned. Reposts credit nobody: the reposter
        didn't write it, and the original author is found by their own post."""
        kws = {keywords} if isinstance(keywords, str) else set(keywords)
        for p in found:
            if p.kind == "repost" or any(x in p.text.lower() for x in self._excludes):
                continue
            c = candidate_from_author(p.author, source)
            c.matched_posts = 1
            c.matched_keywords = set(kws)
            c.sample_text = p.text[:SAMPLE_CHARS]
            c.sample_url = p.url
            self._add(c)

    def add_profiles(self, keyword: str, found: Iterable[Author], source: str) -> None:
        """Accounts a profile/channel/show search returned for a keyword."""
        for a in found:
            c = candidate_from_author(a, source)
            c.matched_keywords = {keyword}
            self._add(c)

    def add(self, c: Candidate) -> None:
        self._add(c)

    def enrich(self, profiles: dict[tuple[str, str], Author]) -> None:
        for key, a in profiles.items():
            c = self.items.get(key)
            if c is None:
                continue
            c.followers = a.followers if a.followers is not None else c.followers
            c.bio = a.bio or c.bio
            c.display_name = a.display_name or c.display_name
            c.avatar_url = a.avatar_url or c.avatar_url
            c.bio_match = c.bio_match or self._bio_matches(c.bio or "")

    def results(self, min_followers: int, existing_seeds: set[tuple[str, str]]) -> list[Candidate]:
        out = []
        for c in self.items.values():
            if (c.platform, c.seed_handle.lower()) in existing_seeds:
                continue
            if any(x in (c.bio or "").lower() for x in self._excludes):
                continue
            # Podcasts have no follower counts; keep them regardless of the threshold.
            if c.platform != "podcast" and (c.followers or 0) < min_followers:
                continue
            out.append(c)
        return sorted(out, key=lambda c: -c.score)


def config_seed_keys(topic: Topic) -> set[tuple[str, str]]:
    return {
        (s.platform, s.handle.lower()) for s in map(SeedAccount.parse, topic.config.seed_accounts)
    }


# ---------------------------------------------------------------- sources


def from_existing(conn: Connection, topic: Topic, pool: CandidatePool, settings: Settings) -> int:
    """Accounts already collected whose posts were classified on-topic for this topic."""
    min_conf = settings.on_topic_min_confidence
    latest = (
        select(
            posts.c.author_id,
            posts.c.text,
            posts.c.url,
            func.row_number()
            .over(partition_by=posts.c.author_id, order_by=posts.c.published_at.desc())
            .label("rn"),
            func.count().over(partition_by=posts.c.author_id).label("n"),
        )
        .select_from(posts.join(post_topics, post_topics.c.post_id == posts.c.id))
        .where(
            post_topics.c.topic_id == topic.id,
            post_topics.c.on_topic,
            post_topics.c.confidence >= min_conf,
            posts.c.kind != "repost",
        )
        .subquery()
    )
    rows = conn.execute(
        select(authors, latest.c.text, latest.c.url.label("post_url"), latest.c.n)
        .join(latest, latest.c.author_id == authors.c.id)
        .where(latest.c.rn == 1, authors.c.platform.in_(["x", "bluesky", "youtube", "podcast"]))
    ).all()
    for r in rows:
        a = Author(
            platform=r.platform,
            platform_id=r.platform_id,
            handle=r.handle,
            display_name=r.display_name,
            bio=r.bio,
            url=r.url,
            avatar_url=r.avatar_url,
            followers=r.followers,
            raw=r.raw,
        )
        c = candidate_from_author(a, "existing")
        c.matched_posts = r.n
        c.sample_text = (r.text or "")[:SAMPLE_CHARS]
        c.sample_url = r.post_url
        pool.add(c)
    return len(rows)


def from_bluesky(collector: Any, topic: Topic, pool: CandidatePool) -> int:
    from app.collectors.bluesky import normalize_author, normalize_post_view

    n = 0
    for kw in topic.config.keywords:
        resp = collector._get("app.bsky.feed.searchPosts", {"q": kw, "limit": 100, "sort": "top"})
        found = [normalize_post_view(pv) for pv in resp.get("posts", [])]
        pool.add_posts(kw, found, "bluesky")
        actors = collector._get("app.bsky.actor.searchActors", {"q": kw, "limit": 50})
        pool.add_profiles(kw, [normalize_author(a) for a in actors.get("actors", [])], "bluesky")
        n += len(found) + len(actors.get("actors", []))
    dids = [pid for (plat, pid) in pool.items if plat == "bluesky"]
    profiles: dict[tuple[str, str], Author] = {}
    for i in range(0, len(dids), 25):
        resp = collector._get("app.bsky.actor.getProfiles", {"actors": dids[i : i + 25]})
        for p in resp.get("profiles", []):
            profiles[("bluesky", p["did"])] = normalize_author(p)
    pool.enrich(profiles)
    return n


def from_x(collector: Any, topic: Topic, pool: CandidatePool, max_reads: int, today: date) -> int:
    from app.collectors.x import (
        EXPANSIONS,
        TWEET_FIELDS,
        USER_FIELDS,
        build_query,
        count_reads,
        normalize_response,
    )

    budget = collector.budget()
    st = budget.status(today)
    reads = min(100, max_reads, st.remaining)
    if reads < 10:
        log.warning(
            "discover_x_skipped",
            topic=topic.slug,
            reason="X budget too low",
            remaining=st.remaining,
        )
        return 0
    resp = collector.client.search_recent_tweets(
        build_query(topic.config.keywords, topic.config.exclude_keywords, include_retweets=False),
        max_results=reads,
        expansions=EXPANSIONS,
        tweet_fields=TWEET_FIELDS,
        user_fields=USER_FIELDS,
        sort_order="relevancy",
    )
    collector.record("discover_search", resp)
    budget.spend(f"discover:{topic.slug}", today, count_reads(resp))
    found = normalize_response(resp)
    matches = {kw: keyword_matcher([kw]) for kw in topic.config.keywords}
    for p in found:
        # One OR-query found it; credit whichever keywords the text actually contains.
        kws = {kw for kw, m in matches.items() if m(p.text)} or {topic.config.keywords[0]}
        pool.add_posts(kws, [p], "x")
    return len(found)


def from_youtube(
    collector: Any,
    engine: Engine,
    topic: Topic,
    pool: CandidatePool,
    settings: Settings,
    today: date,
    n_topics: int,
) -> int:
    from app.collectors.youtube import build_query, normalize_channel

    scope = f"discover:{topic.slug}"
    with engine.connect() as conn:
        allowance = quota_scope_allowance(
            conn, "youtube", scope, today, settings.youtube_daily_search_cap, n_topics + 1
        )
    if allowance <= 0:
        log.warning("discover_youtube_skipped", topic=topic.slug, reason="daily search cap")
        return 0
    with engine.begin() as conn:
        quota_add(conn, "youtube", scope, today, 1)
    resp = collector._get(
        "search",
        {
            "part": "snippet",
            "type": "channel",
            "maxResults": 50,
            "q": build_query(topic.config.keywords),
            "regionCode": "US",
            "relevanceLanguage": "en",
        },
    )
    ids = [
        it["id"]["channelId"] for it in resp.get("items", []) if it.get("id", {}).get("channelId")
    ]
    channels: list[Author] = []
    for i in range(0, len(ids), 50):
        detail = collector._get(
            "channels", {"part": "snippet,statistics", "id": ",".join(ids[i : i + 50])}
        )
        channels += [normalize_channel(c) for c in detail.get("items", [])]
    matcher = {kw: keyword_matcher([kw]) for kw in topic.config.keywords}
    for ch in channels:
        text = f"{ch.display_name or ''} {ch.bio or ''}"
        kws = [kw for kw, m in matcher.items() if m(text)] or topic.config.keywords[:1]
        pool.add_profiles(kws[0], [ch], "youtube")
        pool.items[("youtube", ch.platform_id)].matched_keywords |= set(kws)
    return len(channels)


def from_podcasts(collector: Any, topic: Topic, pool: CandidatePool) -> int:
    from app.collectors.podcasts import show_author

    n = 0
    for kw in topic.config.keywords:
        feeds = collector._api("/search/byterm", {"q": kw, "max": 20, "clean": "true"})
        shows = [show_author(f) for f in feeds.get("feeds", []) if f.get("url")]
        for s in shows:
            c = candidate_from_author(s, "podcasts")
            c.matched_keywords = {kw}
            c.matched_posts = 1  # one matching search per keyword stands in for activity
            pool.add(c)
        n += len(shows)
    return n


# ---------------------------------------------------------------- persistence


def save_candidates(conn: Connection, topic_id: int, cands: list[Candidate]) -> int:
    """Upsert; refreshes stats but leaves status/decided_* alone. Returns rows touched."""
    if not cands:
        return 0
    rows = [
        {
            "topic_id": topic_id,
            "platform": c.platform,
            "platform_id": c.platform_id,
            "seed_handle": c.seed_handle,
            "display_name": c.display_name,
            "bio": c.bio,
            "url": c.url,
            "avatar_url": c.avatar_url,
            "followers": c.followers,
            "matched_posts": c.matched_posts,
            "matched_keywords": sorted(c.matched_keywords),
            "bio_match": c.bio_match,
            "sample_text": c.sample_text,
            "sample_url": c.sample_url,
            "sources": sorted(c.sources),
            "score": round(c.score, 4),
        }
        for c in cands
    ]
    stmt = pg_insert(seed_candidates).values(rows)
    refresh = (
        "seed_handle",
        "display_name",
        "bio",
        "url",
        "avatar_url",
        "followers",
        "matched_posts",
        "matched_keywords",
        "bio_match",
        "sample_text",
        "sample_url",
        "sources",
        "score",
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_seed_candidates",
        set_={**{k: stmt.excluded[k] for k in refresh}, "last_seen": func.now()},
    )
    conn.execute(stmt)
    return len(rows)


def decide(conn: Connection, candidate_id: int, status: str, by: str | None) -> bool:
    res = conn.execute(
        update(seed_candidates)
        .where(seed_candidates.c.id == candidate_id)
        .values(
            status=status,
            decided_by=by if status != "pending" else None,
            decided_at=func.now() if status != "pending" else None,
        )
    )
    return res.rowcount > 0
