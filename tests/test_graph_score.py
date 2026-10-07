from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import insert, select

from app.collectors.base import Author, EdgeHint, Post, store_post
from app.config import Settings
from app.models import author_scores, authors, edges, post_topics, posts
from app.pipeline.graph import Edge, build_graph, pagerank
from app.pipeline.score import AuthorStats, compute_scores, percentile, score_topic


def test_pagerank_two_node_known_answer() -> None:
    # A -> B, alpha .85, B dangling (mass redistributed uniformly):
    # PR(A) = .075 + .425 PR(B), PR(A) + PR(B) = 1  =>  PR(B) = .925 / 1.425
    pr = pagerank(build_graph([Edge(1, 2, "reply")]))
    assert pr[2] == pytest.approx(0.925 / 1.425, abs=1e-6)
    assert pr[1] == pytest.approx(0.5 / 1.425, abs=1e-6)


def test_pagerank_star_and_isolated_nodes() -> None:
    es = [Edge(s, 9, "mention") for s in (1, 2, 3, 4)]
    pr = pagerank(build_graph(es, nodes=[1, 2, 3, 4, 5]))
    assert max(pr, key=pr.__getitem__) == 9
    assert pr[1] == pytest.approx(pr[5])  # spokes and an isolated node are equivalent
    assert sum(pr.values()) == pytest.approx(1.0)


def test_edge_weights_accumulate() -> None:
    g = build_graph([Edge(1, 2, "quote"), Edge(1, 2, "repost"), Edge(1, 3, "reply")])
    assert g[1][2]["weight"] == 5 and g[1][3]["weight"] == 1
    pr = pagerank(g)
    assert pr[2] > pr[3]


def test_percentile() -> None:
    assert percentile([]) == []
    assert percentile([7.0]) == [1.0]
    assert percentile([10, 30, 20]) == [0.0, 1.0, 0.5]
    assert percentile([5, 5, 0]) == [0.75, 0.75, 0.0]


def test_compute_scores_weights_and_rank() -> None:
    stats = [
        AuthorStats(1, on_topic_posts=3, likes=100, followers=10),
        AuthorStats(2, on_topic_posts=1, likes=1, followers=1_000_000),
        AuthorStats(3, on_topic_posts=2, likes=10, followers=100),
    ]
    pr = {1: 0.5, 2: 0.1, 3: 0.4}
    rows = {r.author_id: r for r in compute_scores(stats, pr, Settings())}
    assert rows[1].centrality == 1.0 and rows[1].engagement == 1.0 and rows[1].reach == 0.0
    assert rows[1].composite == pytest.approx(0.8)
    assert rows[2].composite == pytest.approx(0.2)
    assert rows[3].composite == pytest.approx(0.5 * 0.5 + 0.3 * 0.5 + 0.2 * 0.5)
    assert [rows[i].rank for i in (1, 3, 2)] == [1, 2, 3]
    custom = Settings(score_w_centrality=0, score_w_engagement=0, score_w_reach=1)
    top = min(compute_scores(stats, pr, custom), key=lambda r: r.rank)
    assert top.author_id == 2


# ---------------------------------------------------------------- end to end on the DB


def _author(pid: str, followers: int = 100) -> Author:
    return Author(platform="x", platform_id=pid, handle=pid, followers=followers)


@pytest.mark.db
def test_score_topic_end_to_end(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    now = datetime.now(UTC) - timedelta(hours=1)
    reporter = _author("reporter", 5000)
    plan = [
        # (post id, author, on_topic, hints)
        ("p1", reporter, True, []),
        ("p2", _author("fan1"), True, [EdgeHint("quote", "x", author_platform_id="reporter")]),
        ("p3", _author("fan2"), True, [EdgeHint("reply", "x", post_platform_id="p1")]),
        ("p4", _author("fan3"), True, [EdgeHint("mention", "x", handle="REPORTER")]),
        (
            "p5",
            _author("offtopic"),
            False,
            [EdgeHint("repost", "x", author_platform_id="reporter")],
        ),
        (
            "p6",
            _author("selfie"),
            True,
            [EdgeHint("mention", "x", handle="selfie"), EdgeHint("mention", "x", handle="nobody")],
        ),
    ]
    with db.connect() as conn:
        for pid, a, on, hints in plan:
            store_post(
                conn,
                Post(
                    platform="x",
                    platform_id=pid,
                    author=a,
                    text=f"text {pid}",
                    published_at=now,
                    edge_hints=hints,
                    likes=10,
                ),
                topic.id,
            )
            post_id = conn.execute(
                select(posts.c.id).where(posts.c.platform_id == pid)
            ).scalar_one()
            conn.execute(
                insert(post_topics).values(
                    post_id=post_id, topic_id=topic.id, on_topic=on, confidence=0.9
                )
            )
        conn.commit()

        result = score_topic(conn, topic, date.today())
        assert result["edges"] == 3  # off-topic repost, self-mention, unknown handle dropped
        kinds = sorted(conn.execute(select(edges.c.kind)).scalars())
        assert kinds == ["mention", "quote", "reply"]

        rows = conn.execute(
            select(authors.c.handle, author_scores.c.rank, author_scores.c.on_topic_posts)
            .join(authors, authors.c.id == author_scores.c.author_id)
            .order_by(author_scores.c.rank)
        ).all()
        assert rows[0].handle == "reporter"
        assert "offtopic" not in {r.handle for r in rows}
        assert len(rows) == 5

        # re-scoring the same day replaces, doesn't duplicate; edges are rebuilt
        score_topic(conn, topic, date.today())
        assert len(conn.execute(select(author_scores)).all()) == 5
        assert len(conn.execute(select(edges)).all()) == 3
