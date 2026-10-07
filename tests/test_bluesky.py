from datetime import UTC, datetime

import httpx

from app.collectors.bluesky import BlueskyCollector, normalize_post_view
from app.config import Settings
from app.schemas import Topic, TopicConfig
from tests.conftest import load_fixture


def test_normalize_search_results() -> None:
    pvs = load_fixture("bluesky_search_posts.json")["posts"]
    p1, p2, p3 = (normalize_post_view(pv) for pv in pvs)

    assert p1.kind == "post"
    assert p1.author.handle == "energyreporter.bsky.social"
    assert p1.url == "https://bsky.app/profile/energyreporter.bsky.social/post/3lpost1"
    assert [(h.kind, h.author_platform_id) for h in p1.edge_hints] == [
        ("mention", "did:plc:analyst222")
    ]
    assert (p1.likes, p1.reposts, p1.replies, p1.quotes) == (55, 10, 4, 2)

    assert p2.kind == "quote"
    assert p2.parent_platform_id == p1.platform_id
    assert [(h.kind, h.author_platform_id) for h in p2.edge_hints] == [
        ("quote", "did:plc:reporter111")
    ]
    assert [a.platform_id for a in p2.related_authors] == ["did:plc:reporter111"]

    assert p3.kind == "reply"
    assert [(h.kind, h.author_platform_id, h.post_platform_id) for h in p3.edge_hints] == [
        ("reply", "did:plc:reporter111", p1.platform_id)
    ]


def test_author_feed_repost() -> None:
    item = load_fixture("bluesky_author_feed.json")["feed"][0]
    p = normalize_post_view(item["post"], item["reason"], reposter={"did": "x"})
    assert p.kind == "repost"
    assert p.author.platform_id == "did:plc:reporter111"
    assert p.edge_hints[0].kind == "repost"
    assert p.edge_hints[0].author_platform_id == "did:plc:analyst222"
    assert p.platform_id.startswith("repost:did:plc:reporter111:")


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "tok"})
        assert request.headers["Authorization"] == "Bearer tok"
        if path.endswith("searchPosts"):
            if request.url.params.get("cursor"):
                return httpx.Response(200, json={"posts": []})
            return httpx.Response(200, json=load_fixture("bluesky_search_posts.json"))
        if path.endswith("getProfiles"):
            return httpx.Response(200, json=load_fixture("bluesky_get_profiles.json"))
        if path.endswith("getAuthorFeed"):
            return httpx.Response(200, json=load_fixture("bluesky_author_feed.json"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _topic(**kw) -> Topic:  # type: ignore[no-untyped-def]
    cfg = TopicConfig(slug="t", name="T", description="d", keywords=["RGGI", "PJM"], **kw)
    return Topic(id=1, slug="t", name="T", description="d", window_days=30, active=True, config=cfg)


def test_collect_dedupes_across_keywords_and_enriches_followers() -> None:
    c = BlueskyCollector(
        settings=Settings(bluesky_handle="h", bluesky_app_password="p"),
        http=httpx.Client(transport=_mock_transport()),
    )
    c.limiter.interval = 0
    posts = list(c.collect(_topic(), datetime(2026, 9, 1, tzinfo=UTC)))
    assert len(posts) == 3  # same 3 posts returned for both keywords
    by_handle = {p.author.handle: p.author.followers for p in posts}
    assert by_handle["energyreporter.bsky.social"] == 5400
    assert posts[1].related_authors[0].followers == 5400


def test_collect_seed_feed_respects_since_and_excludes() -> None:
    c = BlueskyCollector(
        settings=Settings(bluesky_handle="h", bluesky_app_password="p"),
        http=httpx.Client(transport=_mock_transport()),
    )
    c.limiter.interval = 0
    topic = _topic(
        seed_accounts=["bluesky:energyreporter.bsky.social"], exclude_keywords=["my bill"]
    )
    posts = list(c.collect(topic, datetime(2026, 9, 1, tzinfo=UTC)))
    ids = [p.platform_id for p in posts]
    assert not any("3lpost8" in i for i in ids)  # older than since
    assert any(i.startswith("repost:") for i in ids)
    assert not any("3lpost3" in i for i in ids)  # excluded keyword
