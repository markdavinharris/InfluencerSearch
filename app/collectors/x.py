"""X (Twitter) collector — API v2 via tweepy, Basic tier, budget-guarded.

Order of spend for each topic: seed-account timelines first (cheap, high value), then one
OR-joined recent-search query built from the topic keywords. Stops when the topic's daily
allocation (see x_budget.py) is used up.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.collectors.base import (
    Author,
    BudgetExhausted,
    Collector,
    CollectorNotConfigured,
    EdgeHint,
    EdgeKind,
    Post,
    parse_dt,
)
from app.collectors.x_budget import XBudget
from app.db import get_engine
from app.log import get_logger
from app.schemas import Topic

log = get_logger(__name__)

TWEET_FIELDS = [
    "public_metrics",
    "created_at",
    "referenced_tweets",
    "entities",
    "author_id",
    "in_reply_to_user_id",
    "conversation_id",
    "lang",
]
USER_FIELDS = ["public_metrics", "description", "profile_image_url", "username", "name", "url"]
EXPANSIONS = [
    "author_id",
    "referenced_tweets.id",
    "referenced_tweets.id.author_id",
    "entities.mentions.username",
    "in_reply_to_user_id",
]
MAX_QUERY_LEN = 512  # Basic tier search query length limit


def _quote(term: str) -> str:
    term = term.strip()
    return f'"{term}"' if (" " in term and not term.startswith('"')) else term


def build_query(keywords: list[str], exclude: list[str], include_retweets: bool = True) -> str:
    """One OR-joined query per topic, trimmed to the query length limit by dropping trailing
    keywords (keywords are listed in priority order in the topic YAML)."""
    suffix_parts = [f"-{_quote(x)}" for x in exclude]
    if not include_retweets:
        suffix_parts.append("-is:retweet")
    suffix_parts.append("lang:en")
    suffix = " ".join(suffix_parts)
    terms = [_quote(k) for k in keywords]
    while terms:
        q = f"({' OR '.join(terms)}) {suffix}"
        if len(q) <= MAX_QUERY_LEN:
            return q
        terms.pop()
    raise ValueError("topic keywords too long to build an X query")


def normalize_user(u: dict[str, Any]) -> Author:
    pm = u.get("public_metrics") or {}
    return Author(
        platform="x",
        platform_id=str(u["id"]),
        handle=u.get("username"),
        display_name=u.get("name"),
        bio=u.get("description"),
        url=f"https://x.com/{u['username']}" if u.get("username") else None,
        avatar_url=u.get("profile_image_url"),
        followers=pm.get("followers_count"),
        following=pm.get("following_count"),
        raw=u,
    )


def normalize_response(resp: dict[str, Any]) -> list[Post]:
    """Normalize one page of a v2 tweets response (search or user timeline)."""
    includes = resp.get("includes") or {}
    users = {str(u["id"]): u for u in includes.get("users", [])}
    ref_tweets = {str(t["id"]): t for t in includes.get("tweets", [])}
    out: list[Post] = []
    for t in resp.get("data") or []:
        author_raw = users.get(str(t["author_id"]))
        if author_raw is None:
            continue
        pm = t.get("public_metrics") or {}
        kind = "post"
        parent: str | None = None
        hints: list[EdgeHint] = []
        related_ids: set[str] = set()
        refs = t.get("referenced_tweets") or []
        for ref in refs:
            edge_kind: EdgeKind
            ref_id = str(ref["id"])
            ref_author = str((ref_tweets.get(ref_id) or {}).get("author_id") or "") or None
            if ref["type"] == "retweeted":
                kind, edge_kind = "repost", "repost"
            elif ref["type"] == "quoted":
                kind, edge_kind = ("quote" if kind == "post" else kind), "quote"
            elif ref["type"] == "replied_to":
                kind, edge_kind = ("reply" if kind == "post" else kind), "reply"
                ref_author = ref_author or (
                    str(t["in_reply_to_user_id"]) if t.get("in_reply_to_user_id") else None
                )
            else:
                continue
            parent = parent or ref_id
            hints.append(
                EdgeHint(
                    kind=edge_kind,
                    platform="x",
                    author_platform_id=ref_author,
                    post_platform_id=ref_id,
                )
            )
            if ref_author:
                related_ids.add(ref_author)
        # Mentions in a retweet belong to the original tweet, not the retweeter.
        if kind != "repost":
            already = {h.author_platform_id for h in hints}
            for m in (t.get("entities") or {}).get("mentions", []):
                mid = str(m["id"]) if m.get("id") else None
                if mid and mid in already:
                    continue  # a reply's leading @mention is already a reply edge
                hints.append(
                    EdgeHint(
                        kind="mention",
                        platform="x",
                        author_platform_id=mid,
                        handle=m.get("username"),
                    )
                )
                if mid:
                    related_ids.add(mid)
        author = normalize_user(author_raw)
        related_ids.discard(author.platform_id)
        out.append(
            Post(
                platform="x",
                platform_id=str(t["id"]),
                author=author,
                text=t.get("text", ""),
                url=f"https://x.com/{author.handle}/status/{t['id']}",
                published_at=parse_dt(t.get("created_at")) or datetime.now(UTC),
                likes=pm.get("like_count", 0),
                reposts=pm.get("retweet_count", 0),
                replies=pm.get("reply_count", 0),
                quotes=pm.get("quote_count", 0),
                views=pm.get("impression_count"),
                kind=kind,  # type: ignore[arg-type]
                parent_platform_id=parent,
                edge_hints=hints,
                raw=t,
                related_authors=[normalize_user(users[i]) for i in related_ids if i in users],
            )
        )
    return out


def count_reads(resp: dict[str, Any]) -> int:
    return len(resp.get("data") or []) + len((resp.get("includes") or {}).get("tweets", []))


class XCollector(Collector):
    platform = "x"

    def __init__(
        self, *args: Any, n_topics: int = 1, today: date | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.n_topics = n_topics
        self.today = today or datetime.now(UTC).date()
        self._client: Any = None

    def is_configured(self) -> bool:
        return bool(self.settings.x_bearer_token)

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.is_configured():
                raise CollectorNotConfigured("X_BEARER_TOKEN not set")
            import tweepy

            self._client = tweepy.Client(
                bearer_token=self.settings.x_bearer_token,
                return_type=dict,
                wait_on_rate_limit=True,
            )
        return self._client

    def budget(self) -> XBudget:
        return XBudget(
            self.engine or get_engine(), self.settings.x_monthly_post_budget, self.n_topics
        )

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        budget = self.budget()
        allowance = budget.remaining_today(topic.slug, self.today)
        log.info("x_allowance", topic=topic.slug, allowance=allowance)
        if allowance <= 0:
            raise BudgetExhausted("daily X allocation already used for this topic")
        # Recent search only covers the last 7 days.
        earliest = datetime.now(UTC) - timedelta(days=7) + timedelta(minutes=1)
        start = max(since, earliest)

        def take(resp: dict[str, Any], label: str) -> list[Post]:
            nonlocal allowance
            self.record(label, resp)
            n = count_reads(resp)
            budget.spend(topic.slug, self.today, n)
            allowance -= n
            return normalize_response(resp)

        # 1. Seed timelines
        for handle in topic.config.seeds_for("x"):
            if allowance < 5:
                raise BudgetExhausted("allocation used on seed timelines")
            user = self.client.get_user(username=handle)
            if not user.get("data"):
                log.warning("x_seed_not_found", handle=handle)
                continue
            token = None
            while allowance >= 5:
                resp = self.client.get_users_tweets(
                    user["data"]["id"],
                    start_time=start,
                    max_results=max(5, min(100, allowance)),
                    pagination_token=token,
                    expansions=EXPANSIONS,
                    tweet_fields=TWEET_FIELDS,
                    user_fields=USER_FIELDS,
                )
                yield from take(resp, f"timeline_{handle}")
                token = (resp.get("meta") or {}).get("next_token")
                if not token:
                    break

        # 2. Keyword search
        query = build_query(
            topic.config.keywords,
            topic.config.exclude_keywords,
            self.settings.x_include_retweets,
        )
        token = None
        while True:
            if allowance < 10:
                raise BudgetExhausted("daily X allocation reached during search")
            resp = self.client.search_recent_tweets(
                query,
                start_time=start,
                max_results=max(10, min(100, allowance)),
                next_token=token,
                expansions=EXPANSIONS,
                tweet_fields=TWEET_FIELDS,
                user_fields=USER_FIELDS,
            )
            yield from take(resp, "search")
            token = (resp.get("meta") or {}).get("next_token")
            if not token:
                return
