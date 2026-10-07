"""On-topic / stance / summary classification.

Candidates are (post, topic) pairs from `post_sources` with no `post_topics` row yet, for
active topics, inside the topic window. Before the LLM:

- posts shorter than 15 chars and posts matching `exclude_keywords` are written as
  off-topic with model='prefilter';
- exact-duplicate texts (after stripping a leading "RT @user:") are sent once and the
  result is copied to the duplicates, so retweets keep their repost edges without paying
  for the same text twice.

Batches of up to LLM_BATCH_SIZE posts go to the LLM with the topic description. The response
must be a JSON array validated with pydantic; on a parse/validation failure the batch is
retried once as two halves. Posts that still fail are left unclassified for the next run.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Connection, and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.llm import ChatClient, TokenCapExceeded
from app.log import get_logger
from app.models import authors, post_sources, post_topics, posts
from app.schemas import Classification, Topic

log = get_logger(__name__)

MIN_CHARS = 15
MAX_POST_CHARS = 1500
_RT_PREFIX = re.compile(r"^RT @\w+:\s*")
_WS = re.compile(r"\s+")


@dataclass
class Candidate:
    post_id: int
    text: str
    platform: str
    kind: str
    author: str


SYSTEM_PROMPT = """You classify social media posts, news articles, videos and podcast episodes \
for relevance to one topic.

TOPIC: {name}
DEFINITION:
{description}

For EVERY item you are given, decide:
- on_topic: true only if the item is substantively about the topic as defined above \
(passing mentions, keyword collisions, and content about other places/issues are false)
- confidence: 0.0-1.0, how sure you are about on_topic
- stance: the author's stance toward the main policy/action/proposal under discussion: \
"support", "oppose", "neutral" (informational/reporting), or "unclear"
- summary: one short line (max 20 words) saying what the item says about the topic; \
empty string if off-topic

Respond with ONLY a JSON array, one object per input item, in the same order, no prose and no \
code fences:
[{{"post_id": 123, "on_topic": true, "confidence": 0.9, "stance": "oppose", "summary": "..."}}]"""


def dedupe_key(text: str) -> str:
    return _WS.sub(" ", _RT_PREFIX.sub("", text)).strip().lower()


def prefilter_reason(c: Candidate, exclude_keywords: Sequence[str]) -> str | None:
    text = _RT_PREFIX.sub("", c.text).strip()
    if len(text) < MIN_CHARS:
        return "too short"
    low = c.text.lower()
    for kw in exclude_keywords:
        if kw.lower() in low:
            return f"excluded keyword: {kw}"
    return None


def build_user_message(batch: Sequence[Candidate]) -> str:
    items = [
        {
            "post_id": c.post_id,
            "platform": c.platform,
            "type": c.kind,
            "author": c.author,
            "text": c.text[:MAX_POST_CHARS],
        }
        for c in batch
    ]
    return "Classify these items:\n" + json.dumps(items, ensure_ascii=False)


_CLASSIFICATIONS = TypeAdapter(list[Classification])
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_response(text: str, expected_ids: set[int]) -> list[Classification]:
    """Parse and validate the model's JSON. Raises ValueError if unusable. Ignores ids that
    weren't asked about; missing ids are simply absent from the result."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array in response")
    try:
        data = json.loads(cleaned[start : end + 1])
        items = _CLASSIFICATIONS.validate_python(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid classification JSON: {exc}") from exc
    return [c for c in items if c.post_id in expected_ids]


class Classifier:
    def __init__(
        self, llm: ChatClient, batch_size: int | None = None, purpose: str = "classify"
    ) -> None:
        self.llm = llm
        self.batch_size = batch_size or get_settings().llm_batch_size
        self.purpose = purpose

    def classify_batch(
        self, topic: Topic, batch: Sequence[Candidate], retry: bool = True
    ) -> list[Classification]:
        system = SYSTEM_PROMPT.format(name=topic.name, description=topic.description.strip())
        ids = {c.post_id for c in batch}
        try:
            res = self.llm.chat(
                system,
                build_user_message(batch),
                purpose="classify",
                max_tokens=150 * len(batch) + 200,
            )
            out = parse_response(res.text, ids)
        except ValueError as exc:
            if not retry:
                log.warning(
                    "classify_parse_failed", topic=topic.slug, n=len(batch), error=str(exc)[:300]
                )
                return []
            log.info("classify_retry_smaller", topic=topic.slug, n=len(batch))
            if len(batch) == 1:
                return self.classify_batch(topic, batch, retry=False)
            mid = len(batch) // 2
            return self.classify_batch(topic, batch[:mid], retry=False) + self.classify_batch(
                topic, batch[mid:], retry=False
            )
        missing = ids - {c.post_id for c in out}
        if missing and retry and len(missing) < len(batch):
            again = [c for c in batch if c.post_id in missing]
            out += self.classify_batch(topic, again, retry=False)
        return out

    def classify(
        self, topic: Topic, candidates: Sequence[Candidate]
    ) -> tuple[list[Classification], dict[int, str]]:
        """Returns (LLM classifications incl. copies for duplicates, prefiltered id->reason)."""
        prefiltered: dict[int, str] = {}
        unique: dict[str, Candidate] = {}
        dupes: dict[int, int] = {}  # dup post_id -> representative post_id
        for c in candidates:
            reason = prefilter_reason(c, topic.config.exclude_keywords)
            if reason:
                prefiltered[c.post_id] = reason
                continue
            key = dedupe_key(c.text)
            if key in unique:
                dupes[c.post_id] = unique[key].post_id
            else:
                unique[key] = c
        reps = list(unique.values())
        results: list[Classification] = []

        def with_dupes() -> list[Classification]:
            by_id = {r.post_id: r for r in results}
            copies = [
                by_id[rep].model_copy(update={"post_id": dup})
                for dup, rep in dupes.items()
                if rep in by_id
            ]
            return results + copies

        try:
            for i in range(0, len(reps), self.batch_size):
                results += self.classify_batch(topic, reps[i : i + self.batch_size])
        except TokenCapExceeded as exc:
            exc.partial = (with_dupes(), prefiltered)  # type: ignore[attr-defined]
            raise
        return with_dupes(), prefiltered


# ---------------------------------------------------------------- DB side


def fetch_candidates(conn: Connection, topic: Topic, limit: int | None = None) -> list[Candidate]:
    q = (
        select(
            posts.c.id,
            posts.c.text,
            posts.c.platform,
            posts.c.kind,
            func.coalesce(authors.c.display_name, authors.c.handle, "").label("author"),
        )
        .select_from(
            post_sources.join(posts, posts.c.id == post_sources.c.post_id)
            .join(authors, authors.c.id == posts.c.author_id)
            .outerjoin(
                post_topics,
                and_(post_topics.c.post_id == posts.c.id, post_topics.c.topic_id == topic.id),
            )
        )
        .where(
            post_sources.c.topic_id == topic.id,
            post_topics.c.post_id.is_(None),
            posts.c.published_at >= func.now() - func.make_interval(0, 0, 0, topic.window_days),
        )
        .order_by(posts.c.published_at.desc())
    )
    if limit:
        q = q.limit(limit)
    return [
        Candidate(post_id=r.id, text=r.text, platform=r.platform, kind=r.kind, author=r.author)
        for r in conn.execute(q)
    ]


def save_results(
    conn: Connection,
    topic_id: int,
    results: list[Classification],
    prefiltered: dict[int, str],
    model: str,
) -> None:
    rows = [
        {
            "post_id": r.post_id,
            "topic_id": topic_id,
            "on_topic": r.on_topic,
            "confidence": r.confidence,
            "stance": r.stance if r.on_topic else None,
            "summary": r.summary or None,
            "model": model,
        }
        for r in results
    ] + [
        {
            "post_id": pid,
            "topic_id": topic_id,
            "on_topic": False,
            "confidence": 1.0,
            "stance": None,
            "summary": reason,
            "model": "prefilter",
        }
        for pid, reason in prefiltered.items()
    ]
    if not rows:
        return
    stmt = pg_insert(post_topics).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[post_topics.c.post_id, post_topics.c.topic_id],
        set_={
            k: stmt.excluded[k]
            for k in ("on_topic", "confidence", "stance", "summary", "model", "classified_at")
        },
    )
    conn.execute(stmt)


def classify_topic(
    conn: Connection,
    topic: Topic,
    classifier: Classifier,
    chunk: int = 200,
    limit: int | None = None,
) -> dict[str, int]:
    """Classify all pending candidates for a topic in chunks, committing after each chunk.
    Raises TokenCapExceeded (after saving finished chunks) if the daily cap is hit."""
    stats = {"candidates": 0, "classified": 0, "prefiltered": 0, "on_topic": 0}
    cands = fetch_candidates(conn, topic, limit)
    conn.commit()
    stats["candidates"] = len(cands)
    model = classifier.llm.model
    for i in range(0, len(cands), chunk):
        part = cands[i : i + chunk]
        try:
            results, pre = classifier.classify(topic, part)
        except TokenCapExceeded as exc:
            results, pre = getattr(exc, "partial", ([], {}))
            save_results(conn, topic.id, results, pre, model)
            conn.commit()
            log.error("llm_token_cap_reached", topic=topic.slug, saved=len(results), **stats)
            raise
        save_results(conn, topic.id, results, pre, model)
        conn.commit()
        stats["classified"] += len(results)
        stats["prefiltered"] += len(pre)
        stats["on_topic"] += sum(1 for r in results if r.on_topic)
    return stats
