from datetime import date

import pytest

from app.collectors.x import build_query, count_reads, normalize_response
from app.collectors.x_budget import (
    XBudget,
    days_left_in_month,
    topic_daily_allocation,
)
from tests.conftest import load_fixture


def test_normalize_kinds_and_metrics() -> None:
    posts = {p.platform_id: p for p in normalize_response(load_fixture("x_search_recent.json"))}
    assert len(posts) == 4
    orig = posts["1844000000000000001"]
    assert orig.kind == "post"
    assert orig.author.handle == "energyreporter"
    assert orig.author.followers == 15400
    assert (orig.likes, orig.reposts, orig.replies, orig.quotes, orig.views) == (
        210,
        41,
        12,
        7,
        18234,
    )
    assert orig.url == "https://x.com/energyreporter/status/1844000000000000001"
    assert orig.edge_hints == []

    quote = posts["1844000000000000002"]
    assert quote.kind == "quote"
    assert quote.parent_platform_id == "1844000000000000001"
    assert [(h.kind, h.author_platform_id) for h in quote.edge_hints] == [("quote", "1001")]
    assert [a.handle for a in quote.related_authors] == ["energyreporter"]


def test_retweet_has_repost_edge_but_no_mention_edge() -> None:
    posts = {p.platform_id: p for p in normalize_response(load_fixture("x_search_recent.json"))}
    rt = posts["1844000000000000003"]
    assert rt.kind == "repost"
    assert [(h.kind, h.author_platform_id) for h in rt.edge_hints] == [("repost", "1001")]


def test_reply_dedupes_leading_mention_and_keeps_others() -> None:
    posts = {p.platform_id: p for p in normalize_response(load_fixture("x_search_recent.json"))}
    reply = posts["1844000000000000004"]
    assert reply.kind == "reply"
    kinds = sorted((h.kind, h.author_platform_id) for h in reply.edge_hints)
    assert kinds == [("mention", "2001"), ("mention", "2002"), ("reply", "1001")]
    assert {a.handle for a in reply.related_authors} == {"energyreporter", "PAPUC", "GovShapiro"}


def test_count_reads_includes_expanded_tweets() -> None:
    assert count_reads(load_fixture("x_search_recent.json")) == 5


def test_build_query() -> None:
    q = build_query(["Pennsylvania energy", "RGGI"], ["Pennsylvania Avenue"])
    assert q == '("Pennsylvania energy" OR RGGI) -"Pennsylvania Avenue" lang:en'
    assert "-is:retweet" in build_query(["RGGI"], [], include_retweets=False)


def test_build_query_trims_to_limit() -> None:
    kws = [f"keyword number {i}" for i in range(60)]
    q = build_query(kws, [])
    assert len(q) <= 512
    assert '"keyword number 0"' in q and '"keyword number 59"' not in q


# ---------------------------------------------------------------- budget allocator


def test_days_left_in_month() -> None:
    assert days_left_in_month(date(2026, 10, 1)) == 31
    assert days_left_in_month(date(2026, 10, 31)) == 1
    assert days_left_in_month(date(2028, 2, 28)) == 2  # leap year


def test_allocation_splits_evenly() -> None:
    # 9300 left, 31 days, 3 topics -> 100 each
    assert topic_daily_allocation(9300, 0, 0, date(2026, 10, 1), 3) == 100


def test_allocation_uses_start_of_day_remaining() -> None:
    # Topic A already spent 100 today; topic B's allocation must not shrink because of it.
    before = topic_daily_allocation(9300, 0, 0, date(2026, 10, 1), 3)
    after = topic_daily_allocation(9300, 100, 100, date(2026, 10, 1), 3)
    assert before == after == 100


def test_allocation_catches_up_after_underspend_and_never_negative() -> None:
    # Spent nothing for the first 20 days: remaining 8000 over 11 days, 2 topics
    assert topic_daily_allocation(8000, 0, 0, date(2026, 10, 21), 2) == 363
    assert topic_daily_allocation(8000, 9000, 0, date(2026, 10, 21), 2) == 0
    assert topic_daily_allocation(8000, 0, 0, date(2026, 10, 21), 0) == 0


@pytest.mark.db
def test_budget_tracking_roundtrip(db) -> None:  # type: ignore[no-untyped-def]
    today = date(2026, 10, 7)
    b = XBudget(db, monthly_budget=2500, n_topics=2)
    # 2500 left / 25 days / 2 topics = 50
    assert b.status(today).daily_allocation_per_topic == 50
    assert b.remaining_today("a", today) == 50
    b.spend("a", today, 30)
    b.spend("a", today, 15)
    b.spend("b", today, 10)
    st = b.status(today)
    assert st.posts_read == 55
    assert st.daily_allocation_per_topic == 50  # unchanged within the day
    assert b.remaining_today("a", today) == 5
    assert b.remaining_today("b", today) == 40
    # next day: 2445 left / 24 days / 2 topics
    assert b.status(date(2026, 10, 8)).daily_allocation_per_topic == 50
    # new month resets
    assert b.status(date(2026, 11, 1)).posts_read == 0
