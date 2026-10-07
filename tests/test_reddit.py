from app.collectors.reddit import normalize_comment_tree, normalize_submission
from tests.conftest import load_fixture


def test_normalize_submissions() -> None:
    children = [c["data"] for c in load_fixture("reddit_search.json")["data"]["children"]]
    posts = [normalize_submission(d) for d in children]
    selfpost, linkpost, deleted = posts
    assert deleted is None
    assert selfpost is not None and linkpost is not None
    assert selfpost.platform_id == "t3_1abc"
    assert selfpost.author.platform_id == "steelcityowner"
    assert selfpost.text.startswith("Allegheny County reassessment")
    assert "base years" in selfpost.text
    assert selfpost.likes == 154 and selfpost.replies == 87
    assert [(h.kind, h.handle) for h in selfpost.edge_hints] == [("mention", "taxnerd412")]
    assert [(h.kind, h.platform, h.handle) for h in linkpost.edge_hints] == [
        ("link", "news", "publicsource.org")
    ]


def test_comment_tree() -> None:
    posts = normalize_comment_tree(load_fixture("reddit_comments.json"), "steelcityowner")
    assert [p.platform_id for p in posts] == ["t1_c1", "t1_c2"]  # deleted + "more" skipped
    top, reply = posts
    assert top.kind == "reply"
    assert [(h.kind, h.author_platform_id) for h in top.edge_hints] == [("reply", "steelcityowner")]
    assert [(h.kind, h.author_platform_id) for h in reply.edge_hints] == [("reply", "taxnerd412")]
    assert reply.parent_platform_id == "t1_c1"
