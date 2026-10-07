from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.collectors.base import Author, Post, store_post
from app.models import post_topics
from app.pipeline.classify import (
    Candidate,
    Classifier,
    classify_topic,
    dedupe_key,
    parse_response,
    prefilter_reason,
)
from app.schemas import Topic, TopicConfig
from tests.fakes import FakeLLM


def _topic() -> Topic:
    cfg = TopicConfig(
        slug="t",
        name="PA Energy",
        description="PA energy policy",
        keywords=["RGGI"],
        exclude_keywords=["Pennsylvania Avenue"],
    )
    return Topic(
        id=1,
        slug="t",
        name="PA Energy",
        description="PA energy policy",
        window_days=30,
        active=True,
        config=cfg,
    )


def _c(pid: int, text: str) -> Candidate:
    return Candidate(post_id=pid, text=text, platform="x", kind="post", author="a")


def test_prefilter() -> None:
    assert prefilter_reason(_c(1, "RGGI!"), []) == "too short"
    assert prefilter_reason(_c(1, "RT @foo: RGGI"), []) == "too short"
    assert (
        prefilter_reason(_c(1, "Protest on Pennsylvania Avenue today"), ["pennsylvania avenue"])
        == "excluded keyword: pennsylvania avenue"
    )
    assert prefilter_reason(_c(1, "RGGI is back in the PA budget fight"), []) is None


def test_dedupe_key_strips_retweet_prefix() -> None:
    assert dedupe_key("RT @someone: PJM  capacity\nprices") == dedupe_key("PJM capacity prices")


def test_parse_response_handles_fences_and_filters_ids() -> None:
    text = (
        '```json\n[{"post_id": 1, "on_topic": true, "confidence": 0.7, "stance": "support",'
        ' "summary": "x"}, {"post_id": 99, "on_topic": false, "confidence": 0.1}]\n```'
    )
    out = parse_response(text, {1, 2})
    assert [c.post_id for c in out] == [1]
    assert out[0].stance == "support"


@pytest.mark.parametrize(
    "bad",
    [
        "no json here",
        '[{"post_id": 1}]',
        '[{"post_id": 1, "on_topic": true, "confidence": 3}]',
        '[{"post_id": 1, "on_topic": true, "confidence": 0.5, "stance": "angry"}]',
    ],
)
def test_parse_response_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_response(bad, {1})


def test_classifier_dedupes_prefilters_and_batches() -> None:
    llm = FakeLLM()
    clf = Classifier(llm, batch_size=2)
    cands = [
        _c(1, "PJM capacity prices are bad for PA"),
        _c(2, "RT @x: PJM capacity prices are bad for PA"),
        _c(3, "short"),
        _c(4, "Great game last night, go Steelers"),
        _c(5, "RGGI litigation update from Harrisburg"),
    ]
    results, pre = clf.classify(_topic(), cands)
    assert pre == {3: "too short"}
    assert sorted(llm.calls) == [[1, 4], [5]]  # 2 never sent: duplicate of 1
    by_id = {r.post_id: r for r in results}
    assert set(by_id) == {1, 2, 4, 5}
    assert by_id[2].on_topic and by_id[2].stance == "oppose"
    assert not by_id[4].on_topic


def test_classifier_retries_with_smaller_batches_on_garbage() -> None:
    llm = FakeLLM(garbage_calls=1)
    clf = Classifier(llm, batch_size=4)
    cands = [_c(i, f"PJM capacity post number {i}") for i in range(1, 5)]
    results, _ = clf.classify(_topic(), cands)
    assert llm.calls == [[1, 2, 3, 4], [1, 2], [3, 4]]
    assert len(results) == 4


def test_classifier_requeries_missing_ids_once() -> None:
    llm = FakeLLM(drop_ids={3})
    clf = Classifier(llm, batch_size=4)
    cands = [_c(i, f"PJM capacity post number {i}") for i in range(1, 5)]
    results, _ = clf.classify(_topic(), cands)
    assert llm.calls == [[1, 2, 3, 4], [3]]
    assert {r.post_id for r in results} == {1, 2, 4}  # 3 left for the next run


@pytest.mark.db
def test_classify_topic_writes_rows_and_is_incremental(db, topic_factory) -> None:  # type: ignore[no-untyped-def]
    topic = topic_factory()
    now = datetime.now(UTC)
    texts = [
        "PJM capacity auction results are in",
        "unrelated sports chatter here",
        "tiny",
        "RGGI court ruling expected this week",
        "Old RGGI post outside the window",
    ]
    with db.connect() as conn:
        for i, t in enumerate(texts):
            age = timedelta(days=60 if t.startswith("Old") else 1)
            store_post(
                conn,
                Post(
                    platform="x",
                    platform_id=str(i),
                    author=Author(platform="x", platform_id="a1", handle="a1"),
                    text=t,
                    published_at=now - age,
                ),
                topic.id,
            )
        conn.commit()
        llm = FakeLLM()
        stats = classify_topic(conn, topic, Classifier(llm, batch_size=10))
        assert stats == {"candidates": 4, "classified": 3, "prefiltered": 1, "on_topic": 2}
        rows = conn.execute(select(post_topics).order_by(post_topics.c.post_id)).all()
        assert [(r.on_topic, r.model) for r in rows] == [
            (True, "fake-llm"),
            (False, "fake-llm"),
            (False, "prefilter"),
            (True, "fake-llm"),
        ]
        assert rows[1].stance is None  # off-topic rows carry no stance
        again = classify_topic(conn, topic, Classifier(llm, batch_size=10))
        assert again["candidates"] == 0
