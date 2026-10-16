"""YouTube collector — Data API v3 over httpx.

Quota is 10k units/day and `search.list` costs 100, so searches are capped at
YOUTUBE_DAILY_SEARCH_CAP per day (default 30) split across active topics and tracked in
api_quota(platform='youtube', scope=<topic slug>). Each topic gets ONE search per allowance
unit: keywords are OR-joined with `|`. Seed channels are read via their uploads playlist
(1 unit per page) instead of search. Video stats and channel subscriber counts come from
videos.list / channels.list (1 unit per 50 ids). Author = channel. Comments are skipped.
"""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.collectors.base import (
    Author,
    Collector,
    CollectorNotConfigured,
    Post,
    RateLimiter,
    parse_dt,
    quota_add,
    quota_scope_allowance,
    with_retries,
)
from app.db import get_engine
from app.log import get_logger
from app.schemas import Topic

log = get_logger(__name__)

API = "https://www.googleapis.com/youtube/v3"
MAX_DESCRIPTION = 2000
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def build_query(keywords: list[str]) -> str:
    return "|".join(f'"{k}"' if " " in k else k for k in keywords)


def normalize_channel(c: dict[str, Any]) -> Author:
    sn = c.get("snippet") or {}
    st = c.get("statistics") or {}
    handle = sn.get("customUrl")
    thumbs = sn.get("thumbnails") or {}
    return Author(
        platform="youtube",
        platform_id=c["id"],
        handle=handle,
        display_name=sn.get("title"),
        bio=(sn.get("description") or "")[:1000] or None,
        url=f"https://www.youtube.com/{handle}"
        if handle
        else f"https://www.youtube.com/channel/{c['id']}",
        avatar_url=(thumbs.get("default") or {}).get("url"),
        followers=None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
        raw={
            "snippet": {k: sn.get(k) for k in ("title", "customUrl", "country")},
            "statistics": st,
        },
    )


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize_video(v: dict[str, Any], channel: Author) -> Post:
    sn = v.get("snippet") or {}
    st = v.get("statistics") or {}
    desc = (sn.get("description") or "")[:MAX_DESCRIPTION]
    return Post(
        platform="youtube",
        platform_id=v["id"],
        author=channel,
        text=(sn.get("title") or "") + ("\n\n" + desc if desc else ""),
        url=f"https://www.youtube.com/watch?v={v['id']}",
        published_at=parse_dt(sn.get("publishedAt")) or datetime.now(UTC),
        likes=_int(st.get("likeCount")) or 0,
        replies=_int(st.get("commentCount")) or 0,
        views=_int(st.get("viewCount")),
        kind="video",
        raw={
            "snippet": {k: sn.get(k) for k in ("title", "channelId", "publishedAt", "tags")},
            "statistics": st,
        },
    )


class YouTubeCollector(Collector):
    platform = "youtube"

    def __init__(
        self,
        *args: Any,
        http: httpx.Client | None = None,
        n_topics: int = 1,
        today: date | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._http = http
        self.n_topics = n_topics
        self.today = today or datetime.now(UTC).date()
        self.limiter = RateLimiter(per_second=5)

    def is_configured(self) -> bool:
        return bool(self.settings.youtube_api_key)

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=30)
        return self._http

    @with_retries
    def _get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured():
            raise CollectorNotConfigured("YOUTUBE_API_KEY not set")
        self.limiter.wait()
        r = self.http.get(
            f"{API}/{resource}", params={**params, "key": self.settings.youtube_api_key}
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        self.record(resource, data)
        return data

    def _search_allowance(self, topic: Topic) -> int:
        engine = self.engine or get_engine()
        with engine.connect() as conn:
            return quota_scope_allowance(
                conn,
                "youtube",
                topic.slug,
                self.today,
                self.settings.youtube_daily_search_cap,
                self.n_topics,
            )

    def _spend_search(self, topic: Topic) -> None:
        with (self.engine or get_engine()).begin() as conn:
            quota_add(conn, "youtube", topic.slug, self.today, 1)

    def _seed_video_ids(self, handle: str, since: datetime) -> list[str]:
        if CHANNEL_ID_RE.match(handle):  # channels without an @handle are seeded by id
            lookup = {"id": handle}
        else:
            lookup = {"forHandle": handle if handle.startswith("@") else f"@{handle}"}
        ch = self._get("channels", {"part": "contentDetails", **lookup})
        items = ch.get("items") or []
        if not items:
            log.warning("youtube_seed_not_found", handle=handle)
            return []
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        ids: list[str] = []
        token = None
        for _ in range(4):
            page = self._get(
                "playlistItems",
                {
                    "part": "contentDetails",
                    "playlistId": uploads,
                    "maxResults": 50,
                    "pageToken": token,
                },
            )
            for it in page.get("items", []):
                cd = it.get("contentDetails") or {}
                published = parse_dt(cd.get("videoPublishedAt"))
                if published and published >= since:
                    ids.append(cd["videoId"])
            token = page.get("nextPageToken")
            if not token:
                break
        return ids

    def _hydrate(self, video_ids: list[str]) -> list[Post]:
        videos: list[dict[str, Any]] = []
        for i in range(0, len(video_ids), 50):
            videos += self._get(
                "videos", {"part": "snippet,statistics", "id": ",".join(video_ids[i : i + 50])}
            ).get("items", [])
        channel_ids = list({v["snippet"]["channelId"] for v in videos})
        channels: dict[str, Author] = {}
        for i in range(0, len(channel_ids), 50):
            for c in self._get(
                "channels", {"part": "snippet,statistics", "id": ",".join(channel_ids[i : i + 50])}
            ).get("items", []):
                channels[c["id"]] = normalize_channel(c)
        out = []
        for v in videos:
            cid = v["snippet"]["channelId"]
            ch = channels.get(cid) or Author(
                platform="youtube", platform_id=cid, display_name=v["snippet"].get("channelTitle")
            )
            out.append(normalize_video(v, ch))
        return out

    def collect(self, topic: Topic, since: datetime) -> Iterator[Post]:
        ids: list[str] = []
        for handle in topic.config.seeds_for("youtube"):
            ids += self._seed_video_ids(handle, since)

        if self._search_allowance(topic) <= 0:
            log.info("youtube_search_cap_reached", topic=topic.slug)
        else:
            self._spend_search(topic)
            resp = self._get(
                "search",
                {
                    "part": "id",
                    "type": "video",
                    "q": build_query(topic.config.keywords),
                    "publishedAfter": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "maxResults": 50,
                    "order": "relevance",
                    "regionCode": "US",
                    "relevanceLanguage": "en",
                },
            )
            ids += [
                it["id"]["videoId"]
                for it in resp.get("items", [])
                if it.get("id", {}).get("videoId")
            ]

        excludes = [x.lower() for x in topic.config.exclude_keywords]
        for post in self._hydrate(list(dict.fromkeys(ids))):
            if not any(x in post.text.lower() for x in excludes):
                yield post
