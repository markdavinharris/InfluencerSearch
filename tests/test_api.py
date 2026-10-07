"""API tests against a seeded test database via FastAPI's TestClient (httpx)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from app.collectors.base import Author, EdgeHint, Post, store_post
from app.collectors.x_budget import XBudget
from app.llm import log_call
from app.models import collector_runs, post_topics, posts
from app.pipeline.embed import embed_pending
from app.pipeline.score import score_topic
from tests.fakes import FakeEmbedder

KEY = {"X-Internal-Key": "test-key"}
TODAY = datetime.now(UTC).date()


@pytest.fixture
def client(db, topic_factory, monkeypatch) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("INTERNAL_API_KEY", "test-key")
    from app.config import get_settings

    get_settings.cache_clear()
    topic = topic_factory("pa-energy")
    topic_factory("other-topic")
    now = datetime.now(UTC) - timedelta(hours=2)
    rep = Author(
        platform="x", platform_id="1", handle="reporter", display_name="Reporter", followers=5000
    )
    adv = Author(
        platform="bluesky",
        platform_id="did:plc:adv",
        handle="adv.bsky.social",
        display_name="Advocate",
        followers=800,
    )
    fan = Author(platform="x", platform_id="3", handle="fan", followers=10)
    seed = [
        ("p1", rep, "PJM capacity prices are spiking in PA", [], True),
        (
            "p2",
            fan,
            "Great thread on PJM capacity",
            [EdgeHint("quote", "x", author_platform_id="1")],
            True,
        ),
        ("p3", adv, "PJM capacity auction hurts PA families", [], True),
        ("p4", fan, "Steelers!", [], False),
    ]
    with db.connect() as conn:
        for pid, a, text_, hints, on in seed:
            store_post(
                conn,
                Post(
                    platform=a.platform,
                    platform_id=pid,
                    author=a,
                    text=text_,
                    published_at=now,
                    edge_hints=hints,
                    likes=5,
                ),
                topic.id,
            )
            post_id = conn.execute(
                select(posts.c.id).where(posts.c.platform_id == pid)
            ).scalar_one()
            conn.execute(
                insert(post_topics).values(
                    post_id=post_id,
                    topic_id=topic.id,
                    on_topic=on,
                    confidence=0.9,
                    stance="oppose" if on else None,
                    summary=f"summary {pid}" if on else None,
                )
            )
        conn.execute(
            insert(collector_runs).values(
                topic_id=topic.id, platform="x", fetched=4, inserted=4, finished_at=now
            )
        )
        conn.commit()
        embed_pending(conn, FakeEmbedder())
        score_topic(conn, topic, TODAY)
        # A week-old snapshot: same scores except the advocate was lower then.
        conn.execute(
            text("""
            INSERT INTO author_scores (topic_id, author_id, as_of, centrality, engagement,
                                       reach, composite, rank, on_topic_posts, pagerank)
            SELECT topic_id, author_id, as_of - 7, centrality, engagement, reach,
                   CASE WHEN author_id = (SELECT id FROM authors WHERE handle = 'adv.bsky.social')
                        THEN composite - 0.25 ELSE composite END,
                   rank, on_topic_posts, pagerank
            FROM author_scores""")
        )
        conn.commit()
    log_call(db, "classify", "fake", 1000, 200)
    XBudget(db, 8000, 2).spend("pa-energy", TODAY, 120)

    from app.api.main import app

    with TestClient(app) as c:
        yield c


ROUTES = [
    "/topics",
    "/topics/pa-energy/influencers",
    "/topics/pa-energy/rising",
    "/authors/1",
    "/authors/1/score-history?topic=pa-energy",
    "/posts/1/similar",
    "/stats",
]


@pytest.mark.db
@pytest.mark.parametrize("path", ROUTES)
def test_requires_internal_key(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Internal-Key": "wrong"}).status_code == 401
    assert client.get(path, headers=KEY).status_code == 200


@pytest.mark.db
def test_merge_requires_key(client: TestClient) -> None:
    r = client.post("/persons/merge", json={"author_ids": [1], "display_name": "x"})
    assert r.status_code == 401


@pytest.mark.db
def test_health_is_public(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "db": "ok"}


@pytest.mark.db
def test_topics(client: TestClient) -> None:
    topics = {t["slug"]: t for t in client.get("/topics", headers=KEY).json()}
    t = topics["pa-energy"]
    assert t["posts_in_window"] == 4 and t["on_topic_posts_in_window"] == 3
    assert t["last_scored"] == str(TODAY)
    assert t["top_authors"][0]["name"] == "Reporter"
    assert topics["other-topic"]["last_scored"] is None


@pytest.mark.db
def test_influencers(client: TestClient) -> None:
    body = client.get("/topics/pa-energy/influencers", headers=KEY).json()
    assert body["as_of"] == str(TODAY)
    rows = body["rows"]
    assert [r["author"]["handle"] for r in rows][0] == "reporter"
    assert {r["author"]["handle"] for r in rows} == {"reporter", "fan", "adv.bsky.social"}
    top = rows[0]
    assert top["rank"] == 1 and top["delta_7d"] == 0.0 and len(top["sparkline"]) == 2
    adv = next(r for r in rows if r["author"]["handle"] == "adv.bsky.social")
    assert adv["delta_7d"] == pytest.approx(0.25, abs=1e-5)
    for k in ("composite", "centrality", "engagement", "reach"):
        assert 0 <= top[k] <= 1
    bsky = client.get("/topics/pa-energy/influencers?platform=bluesky", headers=KEY).json()
    assert [r["author"]["platform"] for r in bsky["rows"]] == ["bluesky"]
    assert client.get("/topics/nope/influencers", headers=KEY).status_code == 404
    old = client.get(
        f"/topics/pa-energy/influencers?as_of={TODAY - timedelta(days=3)}", headers=KEY
    ).json()
    assert old["as_of"] == str(TODAY - timedelta(days=7))


@pytest.mark.db
def test_rising(client: TestClient) -> None:
    body = client.get("/topics/pa-energy/rising", headers=KEY).json()
    assert body["compared_to"] == str(TODAY - timedelta(days=7))
    assert [r["author"]["handle"] for r in body["rows"]] == ["adv.bsky.social"]
    assert body["rows"][0]["delta"] == pytest.approx(0.25, abs=1e-5)


@pytest.mark.db
def test_author_detail_and_history(client: TestClient) -> None:
    rep = client.get("/topics/pa-energy/influencers", headers=KEY).json()["rows"][0]["author"]
    d = client.get(f"/authors/{rep['id']}", headers=KEY).json()
    assert d["author"]["handle"] == "reporter"
    assert d["scores"][0]["topic_slug"] == "pa-energy"
    assert d["recent_posts"][0]["summary"] == "summary p1"
    assert d["cited_by"][0]["author"]["handle"] == "fan"
    assert d["cited_by"][0]["kinds"] == {"quote": 1}
    assert d["cites"] == []
    h = client.get(f"/authors/{rep['id']}/score-history?topic=pa-energy", headers=KEY).json()
    assert len(h["points"]) == 2
    assert client.get("/authors/99999", headers=KEY).status_code == 404


@pytest.mark.db
def test_merge_persons(client: TestClient) -> None:
    r = client.post(
        "/persons/merge", headers=KEY, json={"author_ids": [1, 2], "display_name": "Same Person"}
    ).json()
    assert r["author_ids"] == [1, 2]
    r2 = client.post(
        "/persons/merge", headers=KEY, json={"author_ids": [2, 3], "display_name": "Renamed"}
    ).json()
    assert r2["person_id"] == r["person_id"] and r2["author_ids"] == [1, 2, 3]
    d = client.get("/authors/1", headers=KEY).json()
    assert d["person"]["display_name"] == "Renamed" and len(d["person"]["accounts"]) == 3
    assert client.post("/persons/unmerge/1", headers=KEY).status_code == 204
    assert client.get("/authors/1", headers=KEY).json()["person"] is None
    bad = client.post(
        "/persons/merge", headers=KEY, json={"author_ids": [1, 999], "display_name": "x"}
    )
    assert bad.status_code == 404


@pytest.mark.db
def test_similar_posts(client: TestClient) -> None:
    sims = client.get("/posts/1/similar", headers=KEY).json()
    assert [s["id"] for s in sims] == [3, 2]  # most shared words first
    assert sims[0]["similarity"] > sims[1]["similarity"]
    assert sims[0]["summary"] == "summary p3"  # p4 is off-topic and excluded


@pytest.mark.db
def test_stats(client: TestClient) -> None:
    s = client.get("/stats", headers=KEY).json()
    assert s["x_budget"]["posts_read"] == 120 and s["x_budget"]["active_topics"] == 2
    assert s["llm_tokens_today"] >= 1200
    assert s["totals"]["posts"] == 4 and s["totals"]["edges"] == 1
    assert s["collector_runs"][0]["topic_slug"] == "pa-energy"
