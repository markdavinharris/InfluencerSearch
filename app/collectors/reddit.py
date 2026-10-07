"""Reddit collector.

Uses Reddit's OAuth JSON API directly over httpx with app-only (client credentials) auth —
the same credentials PRAW would use — so recorded fixtures are the raw listing JSON.

Per keyword: one search across the topic's subreddits (as a multireddit) and one across
r/all, `t=month`. Submissions become posts. For the most-discussed submissions we fetch the
comment tree: top-level comments are posts (reply edge to the submission author), and
second-level replies are posts with a reply edge to the comment they answer. Author reach is
the user profile's follower count (`subreddit.subscribers` on /user/{name}/about).
"""

import re
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

API = "https://oauth.reddit.com"
MAX_THREADS_WITH_COMMENTS = 25
MAX_PROFILE_LOOKUPS = 100
DELETED = {"[deleted]", "[removed]", None, ""}
USER_MENTION_RE = re.compile(r"(?<![\w/])/?u/([A-Za-z0-9_-]{3,20})")


def author_stub(name: str) -> Author:
    return Author(
        platform="reddit",
        platform_id=name.lower(),
        handle=name,
        display_name=name,
        url=f"https://www.reddit.com/user/{name}",
    )


def mention_hints(text: str, self_name: str) -> list[EdgeHint]:
    names = dict.fromkeys(m.group(1) for m in USER_MENTION_RE.finditer(text or ""))
    return [
        EdgeHint(kind="mention", platform="reddit", author_platform_id=n.lower(), handle=n)
        for n in names
        if n.lower() != self_name.lower()
    ]


def normalize_submission(d: dict[str, Any]) -> Post | None:
    if d.get("author") in DELETED:
        return None
    body = d.get("selftext") or ""
    text = d.get("title", "") + ("\n\n" + body if body and body not in DELETED else "")
    hints = mention_hints(text, d["author"])
    if not d.get("is_self") and d.get("url_overridden_by_dest"):
        # link post: the linked domain is the "author" of the shared article
        domain = d.get("domain", "")
        if domain and not domain.startswith(("self.", "i.redd.it", "v.redd.it", "reddit.com")):
            hints.append(EdgeHint(kind="link", platform="news", handle=domain.lower()))
    return Post(
        platform="reddit",
        platform_id=d["name"],  # t3_xxx
        author=author_stub(d["author"]),
        text=text,
        url="https://www.reddit.com" + d.get("permalink", ""),
        published_at=parse_dt(d.get("created_utc")) or datetime.now(UTC),
        likes=max(0, d.get("score", 0)),
        replies=d.get("num_comments", 0),
        kind="post",
        edge_hints=hints,
        raw={
            k: d.get(k)
            for k in (
                "subreddit",
                "score",
                "upvote_ratio",
                "num_comments",
                "permalink",
                "url",
                "domain",
                "is_self",
            )
        },
    )


def normalize_comment(c: dict[str, Any], parent_author: str | None) -> Post | None:
    if c.get("author") in DELETED or c.get("body") in DELETED:
        return None
    hints = []
    if parent_author and parent_author not in DELETED:
        hints.append(
            EdgeHint(
                kind="reply",
                platform="reddit",
                author_platform_id=parent_author.lower(),
                handle=parent_author,
                post_platform_id=c.get("parent_id"),
            )
        )
    hints += mention_hints(c["body"], c["author"])
    return Post(
        platform="reddit",
        platform_id=c["name"],  # t1_xxx
        author=author_stub(c["author"]),
        text=c["body"],
        url="https://www.reddit.com" + c.get("permalink", ""),
        published_at=parse_dt(c.get("created_utc")) or datetime.now(UTC),
        likes=max(0, c.get("score", 0)),
        kind="reply",
        parent_platform_id=c.get("parent_id"),
        edge_hints=hints,
        raw={k: c.get(k) for k in ("subreddit", "score", "permalink", "link_id", "depth")},
    )


def normalize_comment_tree(listing: list[dict[str, Any]], submission_author: str) -> list[Post]:
    """`listing` is the response of /comments/{id}: [submission listing, comment listing]."""
    out: list[Post] = []
    if len(listing) < 2:
        return out
    for child in listing[1]["data"]["children"]:
        if child.get("kind") != "t1":
            continue
        c = child["data"]
        top = normalize_comment(c, submission_author)
        if top:
            out.append(top)
        replies = c.get("replies")
        if not isinstance(replies, dict):
            continue
        for rchild in replies["data"]["children"]:
            if rchild.get("kind") != "t1":
                continue
            r = normalize_comment(rchild["data"], c.get("author"))
            if r:
                out.append(r)
    return out


class RedditCollector(Collector):
    platform = "reddit"

    def __init__(self, *args: Any, http: httpx.Client | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http = http
        self._token: str | None = None
        self.limiter = RateLimiter(per_second=1.5)  # OAuth limit is 100 QPM

    def is_configured(self) -> bool:
        return bool(self.settings.reddit_client_id and self.settings.reddit_client_secret)

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                timeout=30, headers={"User-Agent": self.settings.reddit_user_agent}
            )
        return self._http

    def _login(self) -> None:
        if not self.is_configured():
            raise CollectorNotConfigured("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set")
        r = self.http.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(self.settings.reddit_client_id, self.settings.reddit_client_secret),
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]

    @with_retries
    def _get(self, path: str, params: dict[str, Any], label: str) -> Any:
        if self._token is None:
            self._login()
        self.limiter.wait()
        r = self.http.get(
            API + path,
            params={**params, "raw_json": 1},
            headers={"Authorization": f"bearer {self._token}"},
        )
        if r.status_code == 401:
            self._login()
            r = self.http.get(
                API + path,
                params={**params, "raw_json": 1},
                headers={"Authorization": f"bearer {self._token}"},
            )
        r.raise_for_status()
        data = r.json()
        self.record(label, data)
        return data

    def _search(self, scope: str, query: str, restrict: bool) -> list[dict[str, Any]]:
        params = {"q": query, "t": "month", "sort": "new", "limit": 100, "type": "link"}
        if restrict:
            params["restrict_sr"] = 1
        listing = self._get(f"/r/{scope}/search", params, f"search_{scope}")
        return [c["data"] for c in listing["data"]["children"] if c.get("kind") == "t3"]

    def _followers(self, name: str) -> int | None:
        try:
            about = self._get(f"/user/{name}/about", {}, "about")
        except httpx.HTTPStatusError as exc:  # suspended / shadowbanned users 403/404
            if exc.response.status_code in (403, 404):
                return None
            raise
        sub = (about.get("data") or {}).get("subreddit") or {}
        return sub.get("subscribers")

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        subs = "+".join(topic.config.subreddits)
        submissions: dict[str, dict[str, Any]] = {}
        for kw in topic.config.keywords:
            q = f'"{kw}"' if " " in kw else kw
            if subs:
                for d in self._search(subs, q, restrict=True):
                    submissions[d["name"]] = d
            for d in self._search("all", q, restrict=False):
                submissions[d["name"]] = d

        since_ts = since.timestamp()
        recent = [d for d in submissions.values() if d.get("created_utc", 0) >= since_ts]
        excludes = [x.lower() for x in topic.config.exclude_keywords]

        def excluded(p: Post) -> bool:
            return any(x in p.text.lower() for x in excludes)

        looked_up: dict[str, int | None] = {}

        def with_followers(p: Post) -> Post:
            name = p.author.handle or ""
            if name not in looked_up and len(looked_up) < MAX_PROFILE_LOOKUPS:
                looked_up[name] = self._followers(name)
            p.author.followers = looked_up.get(name)
            return p

        for d in recent:
            post = normalize_submission(d)
            if post and not excluded(post):
                yield with_followers(post)

        busiest = sorted(recent, key=lambda d: d.get("num_comments", 0), reverse=True)
        for d in busiest[:MAX_THREADS_WITH_COMMENTS]:
            if not d.get("num_comments") or d.get("author") in DELETED:
                continue
            tree = self._get(
                f"/comments/{d['id']}",
                {"depth": 2, "limit": 100, "sort": "top"},
                f"comments_{d['id']}",
            )
            for c in normalize_comment_tree(tree, d["author"]):
                if c.published_at.timestamp() >= since_ts and not excluded(c):
                    yield with_followers(c)
