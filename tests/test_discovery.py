"""Seed-account discovery: scoring, sources (mocked APIs), persistence, API."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.collectors.base import Author, Post, store_post
from app.collectors.bluesky import BlueskyCollector
from app.collectors.podcasts import PodcastCollector
from app.collectors.x import XCollector
from app.collectors.youtube import YouTubeCollector
from app.config import Settings
from app.discovery import (
    Candidate,
    CandidatePool,
    config_seed_keys,
    decide,
    from_bluesky,
    from_existing,
    from_podcasts,
    from_x,
    from_youtube,
    save_candidates,
)
from app.models import api_quota, post_topics, posts, seed_candidates, x_budget
from app.schemas import Topic, TopicConfig
from app.topics import active_topics
from tests.conftest import load_fixture

TODAY = date(2026, 10, 7)


def _topic(**kw: Any) -> Topic:
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


def _post(handle: str, text: str, followers: int = 1000, kind: str = "post") -> Post:
    a = Author(platform="x", platform_id=handle, handle=handle, followers=followers)
    return Post(
        platform="x",
        platform_id=f"{handle}-{text[:5]}",
        author=a,
        text=text,
        published_at=datetime.now(UTC),
        kind=kind,
    )  # type: ignore[arg-type]


# ---------------------------------------------------------------- scoring and pooling


def test_score_rewards_followers_activity_bio_and_breadth() -> None:
    base = Candidate("x", "1", "a", followers=10_000)
    assert base.score == pytest.approx(4.0, abs=0.01)  # log10(10001) * (1 + log2(1))
    active = Candidate("x", "1", "a", followers=10_000, matched_posts=3)
    assert active.score == pytest.approx(base.score * 3)  # 1 + log2(4)
    bio = Candidate("x", "1", "a", followers=10_000, bio_match=True)
    assert bio.score == pytest.approx(base.score * 1.5)
    broad = Candidate("x", "1", "a", followers=10_000, matched_keywords={"a", "b", "c"})
    assert broad.score == pytest.approx(base.score * 1.5)
    assert Candidate("x", "1", "a").score == 0


def test_pool_merges_across_keywords_and_skips_reposts_and_excludes() -> None:
    pool = CandidatePool(_topic())
    pool.add_posts(
        "RGGI",
        [
            _post("rep", "RGGI ruling today"),
            _post("rter", "RT @rep: RGGI ruling", kind="repost"),
            _post("dc", "RGGI rally on Pennsylvania Avenue"),
        ],
        "x",
    )
    pool.add_posts("PJM capacity", [_post("rep", "PJM capacity prices up", followers=1200)], "x")
    rep = pool.items[("x", "rep")]
    assert rep.matched_posts == 2
    assert rep.matched_keywords == {"RGGI", "PJM capacity"}
    assert rep.followers == 1200
    assert rep.sample_text == "RGGI ruling today"
    assert set(pool.items) == {("x", "rep")}


def test_pool_results_filter_and_order() -> None:
    topic = _topic(seed_accounts=["x:Already"])
    pool = CandidatePool(topic)
    pool.add(Candidate("x", "1", "big", followers=50_000, sources={"x"}))
    pool.add(Candidate("x", "2", "small", followers=100, matched_posts=9, sources={"x"}))
    pool.add(Candidate("x", "3", "already", followers=90_000, sources={"x"}))
    pool.add(
        Candidate("x", "4", "dc", followers=90_000, bio="I love Pennsylvania Avenue", sources={"x"})
    )
    pool.add(Candidate("podcast", "9", "https://f/x.xml", matched_posts=1, sources={"podcasts"}))
    pool.add(
        Candidate(
            "bluesky",
            "5",
            "bio.bsky.social",
            followers=600,
            bio="Writing about RGGI and PA power",
            sources={"bluesky"},
        )
    )
    out = pool.results(min_followers=500, existing_seeds=config_seed_keys(topic))
    assert [c.seed_handle for c in out] == ["big", "bio.bsky.social", "https://f/x.xml"]
    assert out[1].bio_match


# ---------------------------------------------------------------- sources


def _bluesky_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "tok"})
        name = {
            "searchPosts": "bluesky_search_posts.json",
            "searchActors": "bluesky_search_actors.json",
            "getProfiles": "bluesky_get_profiles.json",
        }[path.rsplit(".", 1)[-1]]
        return httpx.Response(200, json=load_fixture(name))

    return httpx.MockTransport(handler)


def test_from_bluesky() -> None:
    c = BlueskyCollector(
        settings=Settings(bluesky_handle="h", bluesky_app_password="p"),
        http=httpx.Client(transport=_bluesky_transport()),
    )
    c.limiter.interval = 0
    pool = CandidatePool(_topic())
    from_bluesky(c, _topic(), pool)
    puc = pool.items[("bluesky", "did:plc:puc444")]
    assert puc.followers == 9100 and puc.bio_match and puc.matched_posts == 0
    assert puc.seed_handle == "papuc.bsky.social"
    rep = pool.items[("bluesky", "did:plc:reporter111")]
    assert rep.matched_posts == 2  # one post per keyword search (same fixture twice)
    assert rep.followers == 5400
    # posting about it outranks a bigger account that only mentions it in its bio; the
    # 12-follower citizen falls under the threshold
    top = pool.results(500, set())
    assert [c.seed_handle for c in top] == [
        "energyreporter.bsky.social",
        "analyst.bsky.social",
        "papuc.bsky.social",
    ]


class FakeTweepy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search_recent_tweets(self, query: str, **kw: Any) -> dict[str, Any]:
        self.calls.append({"query": query, **kw})
        return load_fixture("x_search_recent.json")


@pytest.mark.db
def test_from_x_spends_budget_under_discover_scope(db) -> None:  # type: ignore[no-untyped-def]
    c = XCollector(
        settings=Settings(x_bearer_token="t", x_monthly_post_budget=8000),
        engine=db,
        n_topics=2,
        today=TODAY,
    )
    c._client = FakeTweepy()
    pool = CandidatePool(_topic())
    assert from_x(c, _topic(), pool, max_reads=50, today=TODAY) == 4
    call = c._client.calls[0]
    assert call["max_results"] == 50 and "-is:retweet" in call["query"]
    assert ("x", "1003") not in pool.items  # the retweeter isn't credited
    reporter = pool.items[("x", "1001")]
    assert reporter.matched_keywords == {"PJM capacity"}
    assert pool.items[("x", "1002")].matched_keywords == {"RGGI"}
    with db.connect() as conn:
        assert conn.execute(select(x_budget.c.posts_read)).scalar() == 5
        scope = conn.execute(select(api_quota.c.scope)).scalar()
    assert scope == "discover:pa-energy"


@pytest.mark.db
def test_from_x_skips_when_budget_exhausted(db) -> None:  # type: ignore[no-untyped-def]
    c = XCollector(
        settings=Settings(x_bearer_token="t", x_monthly_post_budget=5), engine=db, today=TODAY
    )
    c._client = FakeTweepy()
    assert from_x(c, _topic(), CandidatePool(_topic()), 100, TODAY) == 0
    assert c._client.calls == []


@pytest.mark.db
def test_from_youtube_channel_search_respects_cap(db) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        res = req.url.path.rsplit("/", 1)[-1]
        seen.append(res)
        if res == "search":
            assert req.url.params["type"] == "channel"
            return httpx.Response(200, json=load_fixture("youtube_search_channels.json"))
        return httpx.Response(200, json=load_fixture("youtube_channels.json"))

    def make() -> YouTubeCollector:
        y = YouTubeCollector(
            settings=Settings(youtube_api_key="k", youtube_daily_search_cap=2),
            engine=db,
            http=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        y.limiter.interval = 0
        return y

    s = Settings(youtube_daily_search_cap=2)
    pool = CandidatePool(_topic())
    assert from_youtube(make(), db, _topic(), pool, s, TODAY, n_topics=1) == 2
    ke = pool.items[("youtube", "UCaaa")]
    assert ke.followers == 23400 and ke.seed_handle == "@keystoneexplainers"
    seen.clear()
    assert from_youtube(make(), db, _topic(), CandidatePool(_topic()), s, TODAY, 1) == 0
    assert seen == []


def test_from_podcasts() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_fixture("podcastindex_search.json"))

    p = PodcastCollector(
        settings=Settings(podcastindex_key="k", podcastindex_secret="s"),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.limiter.interval = 0
    pool = CandidatePool(_topic())
    from_podcasts(p, _topic(), pool)
    show = pool.items[("podcast", "920666")]
    assert show.seed_handle == "https://feeds.example.com/energyhour.xml"
    assert show.matched_posts == 2 and show.matched_keywords == {"RGGI", "PJM capacity"}


# ---------------------------------------------------------------- DB: existing, persistence


def _on_topic_post(conn, topic_id: int, author: Author, pid: str, on: bool = True) -> None:  # type: ignore[no-untyped-def]
    store_post(
        conn,
        Post(
            platform=author.platform,
            platform_id=pid,
            author=author,
            text=f"post {pid} about RGGI",
            url=f"https://x.com/p/{pid}",
            published_at=datetime.now(UTC) - timedelta(days=int(pid[-1])),
        ),
        topic_id,
    )
    post_id = conn.execute(select(posts.c.id).where(posts.c.platform_id == pid)).scalar_one()
    conn.execute(
        insert(post_topics).values(post_id=post_id, topic_id=topic_id, on_topic=on, confidence=0.9)
    )


@pytest.mark.db
def test_from_existing_and_save_preserves_decisions(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    rep = Author(platform="x", platform_id="1", handle="reporter", followers=5000)
    off = Author(platform="x", platform_id="2", handle="offtopic", followers=9000)
    with db.connect() as conn:
        _on_topic_post(conn, topic.id, rep, "p1")
        _on_topic_post(conn, topic.id, rep, "p2")
        _on_topic_post(conn, topic.id, off, "p3", on=False)
        conn.commit()

        pool = CandidatePool(topic)
        assert from_existing(conn, topic, pool, Settings()) == 1
        c = pool.items[("x", "1")]
        assert c.matched_posts == 2 and c.sample_text == "post p1 about RGGI"
        assert c.sources == {"existing"}

        save_candidates(conn, topic.id, pool.results(0, set()))
        cid = conn.execute(select(seed_candidates.c.id)).scalar_one()
        assert decide(conn, cid, "rejected", "mark@coldspark.com")
        conn.commit()

        # re-discovery refreshes stats but keeps the decision
        c.followers = 6000
        save_candidates(conn, topic.id, [c])
        conn.commit()
        row = conn.execute(select(seed_candidates)).one()
        assert (row.status, row.followers, row.decided_by) == (
            "rejected",
            6000,
            "mark@coldspark.com",
        )
        assert not decide(conn, 9999, "accepted", None)


@pytest.mark.db
def test_accepted_candidates_become_seeds(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory(seed_accounts=["x:fromyaml"])
    with db.connect() as conn:
        save_candidates(
            conn,
            topic.id,
            [
                Candidate("bluesky", "did:1", "a.bsky.social", followers=900),
                Candidate("youtube", "UCaaaaaaaaaaaaaaaaaaaaaa", "UCaaaaaaaaaaaaaaaaaaaaaa"),
                Candidate("x", "3", "notyet", followers=900),
            ],
        )
        ids = (
            conn.execute(select(seed_candidates.c.id).order_by(seed_candidates.c.id))
            .scalars()
            .all()
        )
        decide(conn, ids[0], "accepted", "m")
        decide(conn, ids[1], "accepted", "m")
        conn.commit()
        t = next(t for t in active_topics(conn) if t.slug == topic.slug)
    assert t.config.seed_accounts == [
        "x:fromyaml",
        "bluesky:a.bsky.social",
        "youtube:UCaaaaaaaaaaaaaaaaaaaaaa",
    ]
    assert t.config.seeds_for("bluesky") == ["a.bsky.social"]


# ---------------------------------------------------------------- job + API


@pytest.fixture
def api(db, topic_factory, monkeypatch) -> Iterator[tuple[TestClient, int]]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("INTERNAL_API_KEY", "k")
    from app.config import get_settings

    get_settings.cache_clear()
    topic = topic_factory(seed_accounts=["x:fromyaml"])
    with db.connect() as conn:
        for i, (h, f) in enumerate([("reporter", 5000), ("advocate", 800), ("tiny", 20)]):
            a = Author(
                platform="x", platform_id=str(i), handle=h, display_name=h.title(), followers=f
            )
            _on_topic_post(conn, topic.id, a, f"{h}{i}")
        conn.commit()
    from app.api.main import app

    with TestClient(app) as c:
        yield c, topic.id
    get_settings.cache_clear()


H = {"X-Internal-Key": "k"}


@pytest.mark.db
def test_discover_job_and_review_flow(api) -> None:  # type: ignore[no-untyped-def]
    client, _ = api
    assert client.post("/topics/pa-energy/discover", json={}).status_code == 401
    r = client.post(
        "/topics/pa-energy/discover",
        headers=H,
        json={"sources": ["existing"], "min_followers": 100},
    )
    assert r.status_code == 202  # TestClient runs the background task before returning

    st = client.get("/topics/pa-energy/discover", headers=H).json()
    assert st["running"] is False and st["runs"][0]["platform"] == "seeds:existing"

    body = client.get("/topics/pa-energy/seed-candidates", headers=H).json()
    assert [c["seed_handle"] for c in body["rows"]] == ["reporter", "advocate"]  # tiny < 100
    assert body["counts"] == {"pending": 2, "accepted": 0, "rejected": 0}
    big = client.get("/topics/pa-energy/seed-candidates?min_followers=1000", headers=H).json()
    assert [c["seed_handle"] for c in big["rows"]] == ["reporter"]

    cid = body["rows"][0]["id"]
    d = client.post(
        f"/seed-candidates/{cid}",
        headers=H,
        json={"status": "accepted", "decided_by": "mark@coldspark.com"},
    ).json()
    assert d["status"] == "accepted" and d["decided_by"] == "mark@coldspark.com"
    seeds = client.get("/topics/pa-energy/seeds", headers=H).json()
    assert seeds["from_config"] == ["x:fromyaml"]
    assert [s["seed_handle"] for s in seeds["accepted"]] == ["reporter"]

    # undo
    client.post(f"/seed-candidates/{cid}", headers=H, json={"status": "pending"})
    assert client.get("/topics/pa-energy/seeds", headers=H).json()["accepted"] == []
    assert (
        client.post("/seed-candidates/999", headers=H, json={"status": "accepted"}).status_code
        == 404
    )
    assert client.get("/topics/nope/seed-candidates", headers=H).status_code == 404


@pytest.mark.db
def test_discover_conflict_while_running(api, db) -> None:  # type: ignore[no-untyped-def]
    from app.models import collector_runs

    client, tid = api
    with db.begin() as conn:
        conn.execute(insert(collector_runs).values(topic_id=tid, platform="seeds:x"))
    r = client.post("/topics/pa-energy/discover", headers=H, json={})
    assert r.status_code == 409
