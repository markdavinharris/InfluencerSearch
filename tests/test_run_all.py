import pytest

from app.collectors.base import RunResult
from app.jobs import run_all as ra


@pytest.fixture
def stubs(monkeypatch):  # type: ignore[no-untyped-def]
    calls: list[str] = []
    monkeypatch.setattr(ra, "sync_topics", lambda: calls.append("sync"))
    monkeypatch.setattr(
        ra,
        "run_collect",
        lambda t: (
            calls.append("collect")
            or [RunResult("x", "t", 5, 5), RunResult("bluesky", "t", error="boom")]
        ),
    )
    monkeypatch.setattr(ra, "run_classify", lambda t: calls.append("classify") or True)
    monkeypatch.setattr(ra, "run_embed", lambda: calls.append("embed"))
    monkeypatch.setattr(ra, "run_score", lambda t: calls.append("score"))
    return calls


def test_runs_all_stages_in_order(stubs, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EMBED_API_KEY", "k")
    ra.get_settings.cache_clear()
    assert ra.run_all() is True  # one failing collector doesn't fail the run
    assert stubs == ["sync", "collect", "classify", "embed", "score"]
    ra.get_settings.cache_clear()


def test_failed_stage_still_scores_and_reports_failure(stubs, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(t):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm down")

    monkeypatch.setattr(ra, "run_classify", boom)
    assert ra.run_all(skip={"embed"}) is False
    assert stubs == ["sync", "collect", "score"]


def test_token_cap_and_all_collectors_failing(stubs, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(ra, "run_collect", lambda t: [RunResult("x", "t", error="e")])
    monkeypatch.setattr(ra, "run_classify", lambda t: False)
    assert ra.run_all(skip={"embed"}) is False
    assert "score" in stubs
