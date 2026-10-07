"""YouTube, news and podcast collectors against recorded-format fixtures."""

from datetime import UTC, date, datetime

import httpx
import pytest

from app.collectors.base import keyword_matcher, strip_html
from app.collectors.news import (
    NewsCollector,
    build_gdelt_query,
    link_hints,
    normalize_gdelt,
)
from app.collectors.podcasts import PodcastCollector, auth_headers
from app.collectors.youtube import YouTubeCollector
from app.collectors.youtube import build_query as yt_query
from app.config import Settings
from app.schemas import Topic, TopicConfig
from tests.conftest import FIXTURES, load_fixture

SINCE = datetime(2026, 9, 1, tzinfo=UTC)


def _topic(**kw) -> Topic:  # type: ignore[no-untyped-def]
    data = {
        "slug": "pa-energy",
        "name": "PA Energy",
        "description": "d",
        "keywords": ["RGGI", "PJM capacity"],
        "exclude_keywords": ["Pennsylvania Avenue"],
    }
    cfg = TopicConfig(**(data | kw))
    return Topic(
        id=1, slug=cfg.slug, name=cfg.name, description="d", window_days=30, active=True, config=cfg
    )


def test_helpers() -> None:
    m = keyword_matcher(["RGGI", "PJM capacity"])
    assert m("The rggi ruling") and m("PJM Capacity auction")
    assert not m("RGGIs") and not m("PJM energy")
    assert strip_html("<p>A &amp; <b>B</b></p>") == "A & B"
    h = auth_headers("k", "s", now=1700000000)
    assert h["X-Auth-Date"] == "1700000000"
    assert len(h["Authorization"]) == 40


# ---------------------------------------------------------------- YouTube


def test_youtube_query() -> None:
    assert yt_query(["RGGI", "PJM capacity"]) == 'RGGI|"PJM capacity"'


@pytest.mark.db
def test_youtube_collect_and_search_cap(db) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        res = req.url.path.rsplit("/", 1)[-1]
        calls.append(res)
        assert req.url.params["key"] == "yt"
        return httpx.Response(200, json=load_fixture(f"youtube_{res}.json"))

    def make() -> YouTubeCollector:
        c = YouTubeCollector(
            settings=Settings(youtube_api_key="yt", youtube_daily_search_cap=2),
            engine=db,
            http=httpx.Client(transport=httpx.MockTransport(handler)),
            n_topics=2,
            today=date(2026, 10, 7),
        )
        c.limiter.interval = 0
        return c

    posts = list(make().collect(_topic(), SINCE))
    assert [p.platform_id for p in posts] == ["vid111"]  # vid222 hits exclude keyword
    v = posts[0]
    assert v.kind == "video" and v.views == 15230 and v.likes == 640 and v.replies == 88
    assert v.author.platform_id == "UCaaa" and v.author.followers == 23400
    assert v.author.handle == "@keystoneexplainers"
    assert calls == ["search", "videos", "channels"]

    # cap 2 / 2 topics = 1 search per topic per day: second run skips search entirely
    calls.clear()
    assert list(make().collect(_topic(), SINCE)) == []
    assert "search" not in calls


# ---------------------------------------------------------------- news


def test_gdelt_query() -> None:
    q = build_gdelt_query(["RGGI", "PJM capacity", "PA"], ["Pennsylvania Avenue"])
    assert q == '(RGGI OR "PJM capacity") -"Pennsylvania Avenue" sourcelang:english'


def test_normalize_gdelt() -> None:
    posts = normalize_gdelt(load_fixture("gdelt_artlist.json"))
    assert [p.author.platform_id for p in posts] == ["post-gazette.com", "stateimpact.npr.org"]
    assert posts[0].published_at == datetime(2026, 10, 5, 14, tzinfo=UTC)
    assert posts[0].kind == "article"


def test_link_hints_skip_self_and_social() -> None:
    html = (FIXTURES / "article.html").read_text()
    hints = link_hints(html, "https://www.spotlightpa.org/news/x/", "spotlightpa.org")
    assert [h.handle for h in hints] == ["post-gazette.com", "stateimpact.npr.org"]


def test_news_collect_rss_and_gdelt() -> None:
    article = (FIXTURES / "article.html").read_text()

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://feeds.example.com/spotlight.xml"):
            return httpx.Response(200, content=(FIXTURES / "rss_feed.xml").read_bytes())
        if "api.gdeltproject.org" in url:
            assert "startdatetime=20260901000000" in url
            return httpx.Response(200, json=load_fixture("gdelt_artlist.json"))
        if "stateimpact" in url:
            return httpx.Response(403)  # paywall/blocked: keep headline only
        return httpx.Response(200, text=article)

    c = NewsCollector(
        settings=Settings(news_rss_feeds="https://feeds.example.com/spotlight.xml"),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    c.limiter.interval = 0
    posts = {p.url: p for p in c.collect(_topic(), SINCE)}
    rss = posts["https://www.spotlightpa.org/news/2026/10/shapiro-energy-plan-senate/"]
    assert rss.author.platform_id == "spotlightpa.org"
    assert rss.author.display_name == "Spotlight PA"
    assert "cap-and-invest" in rss.text  # full text from trafilatura
    assert {h.handle for h in rss.edge_hints} == {"post-gazette.com", "stateimpact.npr.org"}
    assert not any("school-boards" in u for u in posts)  # no keyword
    assert not any("/2025/" in u for u in posts)  # before since
    blocked = [p for u, p in posts.items() if "stateimpact" in u][0]
    assert blocked.text == "Court rules on Pennsylvania's RGGI regulation"
    assert len(posts) == 3


# ---------------------------------------------------------------- podcasts


def test_podcast_collect() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.podcastindex.org":
            assert req.headers["X-Auth-Key"] == "pk"
            return httpx.Response(200, json=load_fixture("podcastindex_search.json"))
        return httpx.Response(200, content=(FIXTURES / "podcast_feed.xml").read_bytes())

    c = PodcastCollector(
        settings=Settings(podcastindex_key="pk", podcastindex_secret="ps"),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    c.limiter.interval = 0
    eps = list(c.collect(_topic(), SINCE))
    assert [e.raw["guid"] for e in eps] == ["eh-112"]  # mailbag doesn't mention a keyword
    e = eps[0]
    assert e.kind == "episode" and e.platform == "podcast"
    assert e.author.platform_id == "920666" and e.author.display_name == "The Energy Hour PA"
    assert e.text.startswith("Ep. 112: Is RGGI dead?\n\nWe talk with a former DEP official")
