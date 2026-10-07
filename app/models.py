"""Table definitions (SQLAlchemy Core). Alembic migrations are the source of truth for DDL;
these mirror them for query building."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

EMBED_DIMS = 1536


topics = Table(
    "topics",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("slug", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("config", JSONB, nullable=False, server_default="{}"),
    Column("window_days", Integer, nullable=False, server_default="30"),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

persons = Table(
    "persons",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("notes", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

authors = Table(
    "authors",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("platform", String(16), nullable=False),
    Column("platform_id", Text, nullable=False),
    Column("handle", Text),
    Column("display_name", Text),
    Column("bio", Text),
    Column("url", Text),
    Column("avatar_url", Text),
    Column("followers", Integer),
    Column("following", Integer),
    Column("person_id", Integer, ForeignKey("persons.id", ondelete="SET NULL")),
    Column("raw", JSONB),
    Column("first_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("platform", "platform_id", name="uq_authors_platform_id"),
)
Index("ix_authors_platform_handle", authors.c.platform, func.lower(authors.c.handle))

posts = Table(
    "posts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("platform", String(16), nullable=False),
    Column("platform_id", Text, nullable=False),
    Column("author_id", Integer, ForeignKey("authors.id"), nullable=False),
    Column("text", Text, nullable=False, server_default=""),
    Column("url", Text),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("likes", Integer, nullable=False, server_default="0"),
    Column("reposts", Integer, nullable=False, server_default="0"),
    Column("replies", Integer, nullable=False, server_default="0"),
    Column("quotes", Integer, nullable=False, server_default="0"),
    Column("views", Integer),
    Column("kind", String(16), nullable=False, server_default="post"),
    Column("parent_platform_id", Text),
    # EdgeHints from the collector: [{"kind", "platform", "platform_id" or "handle"}]
    Column("edge_hints", JSONB, nullable=False, server_default="[]"),
    Column("raw", JSONB),
    Column("collected_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("embedding", Vector(EMBED_DIMS)),
    UniqueConstraint("platform", "platform_id", name="uq_posts_platform_id"),
    Index("ix_posts_published_at", "published_at"),
    Index("ix_posts_author_id", "author_id"),
)

# Which topic's collection surfaced a post (retrieval provenance). Classification is
# per (post, topic) for these pairs only.
post_sources = Table(
    "post_sources",
    metadata,
    Column("post_id", BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
    Column("topic_id", Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
    Column("first_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("post_id", "topic_id"),
    Index("ix_post_sources_topic", "topic_id"),
)

post_topics = Table(
    "post_topics",
    metadata,
    Column("post_id", BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
    Column("topic_id", Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
    Column("on_topic", Boolean, nullable=False),
    Column("confidence", Float, nullable=False, server_default="0"),
    Column("stance", Text),
    Column("summary", Text),
    Column("model", Text),
    Column("classified_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("post_id", "topic_id"),
    Index("ix_post_topics_topic_on_topic", "topic_id", "on_topic"),
)

edges = Table(
    "edges",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("topic_id", Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
    Column("src_author_id", Integer, ForeignKey("authors.id"), nullable=False),
    Column("dst_author_id", Integer, ForeignKey("authors.id"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("post_id", BigInteger, ForeignKey("posts.id", ondelete="CASCADE")),
    Column("occurred_at", DateTime(timezone=True)),
    Index("ix_edges_topic_dst", "topic_id", "dst_author_id"),
    Index("ix_edges_topic_src", "topic_id", "src_author_id"),
)

author_scores = Table(
    "author_scores",
    metadata,
    Column("topic_id", Integer, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
    Column("author_id", Integer, ForeignKey("authors.id"), nullable=False),
    Column("as_of", Date, nullable=False),
    Column("centrality", Float, nullable=False),
    Column("engagement", Float, nullable=False),
    Column("reach", Float, nullable=False),
    Column("composite", Float, nullable=False),
    Column("rank", Integer, nullable=False),
    Column("on_topic_posts", Integer, nullable=False),
    Column("pagerank", Float),
    PrimaryKeyConstraint("topic_id", "author_id", "as_of"),
    Index("ix_author_scores_topic_asof_rank", "topic_id", "as_of", "rank"),
)

collector_runs = Table(
    "collector_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("topic_id", Integer, ForeignKey("topics.id", ondelete="CASCADE")),
    Column("platform", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("fetched", Integer, nullable=False, server_default="0"),
    Column("inserted", Integer, nullable=False, server_default="0"),
    Column("error", Text),
)

llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("purpose", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False, server_default="0"),
    Column("output_tokens", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

x_budget = Table(
    "x_budget",
    metadata,
    Column("month", Date, primary_key=True),  # first day of month
    Column("posts_read", Integer, nullable=False, server_default="0"),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Per-day usage of quota-limited APIs that aren't X (e.g. YouTube search.list calls).
api_quota = Table(
    "api_quota",
    metadata,
    Column("platform", String(16), nullable=False),
    Column("scope", Text, nullable=False, server_default=""),
    Column("day", Date, nullable=False),
    Column("units", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("platform", "scope", "day"),
)
