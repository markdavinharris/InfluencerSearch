"""Pydantic models shared by the API and jobs."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PLATFORMS = ("x", "bluesky", "reddit", "youtube", "news", "podcast")
Platform = Literal["x", "bluesky", "reddit", "youtube", "news", "podcast"]
Stance = Literal["support", "oppose", "neutral", "unclear"]


# ---------------------------------------------------------------- topics


class SeedAccount(BaseModel):
    platform: str
    handle: str

    @classmethod
    def parse(cls, value: str) -> "SeedAccount":
        platform, _, handle = value.partition(":")
        if not handle:
            raise ValueError(f"seed account must look like 'platform:handle', got {value!r}")
        return cls(platform=platform.strip().lower(), handle=handle.strip().lstrip("@"))


class TopicConfig(BaseModel):
    """The YAML topic definition (see topics/example.yaml)."""

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    description: str
    keywords: list[str] = Field(min_length=1)
    exclude_keywords: list[str] = []
    subreddits: list[str] = []
    rss_feeds: list[str] = []
    seed_accounts: list[str] = []
    window_days: int = 30
    active: bool = True

    @field_validator("seed_accounts")
    @classmethod
    def _check_seeds(cls, v: list[str]) -> list[str]:
        for s in v:
            SeedAccount.parse(s)
        return v

    def seeds_for(self, platform: str) -> list[str]:
        return [
            s.handle for s in map(SeedAccount.parse, self.seed_accounts) if s.platform == platform
        ]


class Topic(BaseModel):
    """A topic row as loaded from the DB."""

    id: int
    slug: str
    name: str
    description: str
    window_days: int
    active: bool
    config: TopicConfig


# ---------------------------------------------------------------- classification


class Classification(BaseModel):
    post_id: int
    on_topic: bool
    confidence: float = Field(ge=0, le=1)
    stance: Stance = "unclear"
    summary: str = ""


# ---------------------------------------------------------------- API responses


class TopicTopAuthor(BaseModel):
    author_id: int
    name: str
    platform: str


class TopicSummary(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    active: bool
    window_days: int
    posts_in_window: int
    on_topic_posts_in_window: int
    last_scored: date | None
    top_authors: list[TopicTopAuthor]


class AuthorRef(BaseModel):
    id: int
    platform: str
    handle: str | None
    display_name: str | None
    avatar_url: str | None
    url: str | None
    followers: int | None
    person_id: int | None = None


class InfluencerRow(BaseModel):
    rank: int
    author: AuthorRef
    composite: float
    centrality: float
    engagement: float
    reach: float
    on_topic_posts: int
    delta_7d: float | None
    sparkline: list[float] = []


class InfluencersResponse(BaseModel):
    topic: str
    as_of: date | None
    rows: list[InfluencerRow]


class RisingRow(BaseModel):
    author: AuthorRef
    rank: int
    composite: float
    previous_composite: float | None
    delta: float


class RisingResponse(BaseModel):
    topic: str
    as_of: date | None
    compared_to: date | None
    rows: list[RisingRow]


class AuthorTopicScore(BaseModel):
    topic_slug: str
    topic_name: str
    as_of: date
    rank: int
    composite: float
    centrality: float
    engagement: float
    reach: float
    on_topic_posts: int


class AuthorPost(BaseModel):
    id: int
    topic_slug: str
    text: str
    url: str | None
    published_at: datetime
    kind: str
    likes: int
    reposts: int
    replies: int
    quotes: int
    stance: str | None
    summary: str | None
    confidence: float


class EdgeSummary(BaseModel):
    author: AuthorRef
    count: int
    kinds: dict[str, int]


class PersonRef(BaseModel):
    id: int
    display_name: str
    accounts: list[AuthorRef]


class AuthorDetail(BaseModel):
    author: AuthorRef
    bio: str | None
    following: int | None
    first_seen: datetime
    last_seen: datetime
    person: PersonRef | None
    scores: list[AuthorTopicScore]
    recent_posts: list[AuthorPost]
    cited_by: list[EdgeSummary]
    cites: list[EdgeSummary]


class ScorePoint(BaseModel):
    as_of: date
    composite: float
    centrality: float
    engagement: float
    reach: float
    rank: int


class ScoreHistory(BaseModel):
    author_id: int
    topic: str
    points: list[ScorePoint]


class MergeRequest(BaseModel):
    author_ids: list[int] = Field(min_length=1)
    display_name: str = Field(min_length=1)


class MergeResponse(BaseModel):
    person_id: int
    display_name: str
    author_ids: list[int]


class SimilarPost(BaseModel):
    id: int
    author: AuthorRef
    text: str
    url: str | None
    published_at: datetime
    summary: str | None
    similarity: float


class CollectorRunRow(BaseModel):
    id: int
    topic_slug: str | None
    platform: str
    started_at: datetime
    finished_at: datetime | None
    fetched: int
    inserted: int
    error: str | None


class LlmSpendRow(BaseModel):
    day: date
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int


class XBudgetStatus(BaseModel):
    month: date
    monthly_budget: int
    posts_read: int
    remaining: int
    daily_allocation_per_topic: int
    active_topics: int


class Stats(BaseModel):
    collector_runs: list[CollectorRunRow]
    llm_spend: list[LlmSpendRow]
    llm_tokens_today: int
    llm_daily_token_cap: int
    x_budget: XBudgetStatus
    totals: dict[str, int]
