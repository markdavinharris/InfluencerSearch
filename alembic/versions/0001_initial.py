"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE topics (
            id serial PRIMARY KEY,
            slug text NOT NULL UNIQUE,
            name text NOT NULL,
            description text NOT NULL DEFAULT '',
            config jsonb NOT NULL DEFAULT '{}',
            window_days int NOT NULL DEFAULT 30,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE persons (
            id serial PRIMARY KEY,
            display_name text NOT NULL,
            notes text,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE authors (
            id serial PRIMARY KEY,
            platform varchar(16) NOT NULL,
            platform_id text NOT NULL,
            handle text,
            display_name text,
            bio text,
            url text,
            avatar_url text,
            followers int,
            following int,
            person_id int REFERENCES persons(id) ON DELETE SET NULL,
            raw jsonb,
            first_seen timestamptz NOT NULL DEFAULT now(),
            last_seen timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_authors_platform_id UNIQUE (platform, platform_id)
        );
        CREATE INDEX ix_authors_platform_handle ON authors (platform, lower(handle));

        CREATE TABLE posts (
            id bigserial PRIMARY KEY,
            platform varchar(16) NOT NULL,
            platform_id text NOT NULL,
            author_id int NOT NULL REFERENCES authors(id),
            text text NOT NULL DEFAULT '',
            url text,
            published_at timestamptz NOT NULL,
            likes int NOT NULL DEFAULT 0,
            reposts int NOT NULL DEFAULT 0,
            replies int NOT NULL DEFAULT 0,
            quotes int NOT NULL DEFAULT 0,
            views int,
            kind varchar(16) NOT NULL DEFAULT 'post',
            parent_platform_id text,
            edge_hints jsonb NOT NULL DEFAULT '[]',
            raw jsonb,
            collected_at timestamptz NOT NULL DEFAULT now(),
            embedding vector(1536),
            CONSTRAINT uq_posts_platform_id UNIQUE (platform, platform_id)
        );
        CREATE INDEX ix_posts_published_at ON posts (published_at);
        CREATE INDEX ix_posts_author_id ON posts (author_id);

        CREATE TABLE post_sources (
            post_id bigint NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            topic_id int NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            first_seen timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (post_id, topic_id)
        );
        CREATE INDEX ix_post_sources_topic ON post_sources (topic_id);

        CREATE TABLE post_topics (
            post_id bigint NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            topic_id int NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            on_topic boolean NOT NULL,
            confidence real NOT NULL DEFAULT 0,
            stance text,
            summary text,
            model text,
            classified_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (post_id, topic_id)
        );
        CREATE INDEX ix_post_topics_topic_on_topic ON post_topics (topic_id, on_topic);

        CREATE TABLE edges (
            id bigserial PRIMARY KEY,
            topic_id int NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            src_author_id int NOT NULL REFERENCES authors(id),
            dst_author_id int NOT NULL REFERENCES authors(id),
            kind varchar(16) NOT NULL,
            post_id bigint REFERENCES posts(id) ON DELETE CASCADE,
            occurred_at timestamptz
        );
        CREATE INDEX ix_edges_topic_dst ON edges (topic_id, dst_author_id);
        CREATE INDEX ix_edges_topic_src ON edges (topic_id, src_author_id);

        CREATE TABLE author_scores (
            topic_id int NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            author_id int NOT NULL REFERENCES authors(id),
            as_of date NOT NULL,
            centrality real NOT NULL,
            engagement real NOT NULL,
            reach real NOT NULL,
            composite real NOT NULL,
            rank int NOT NULL,
            on_topic_posts int NOT NULL,
            pagerank double precision,
            PRIMARY KEY (topic_id, author_id, as_of)
        );
        CREATE INDEX ix_author_scores_topic_asof_rank ON author_scores (topic_id, as_of, rank);

        CREATE TABLE collector_runs (
            id serial PRIMARY KEY,
            topic_id int REFERENCES topics(id) ON DELETE CASCADE,
            platform varchar(16) NOT NULL,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            fetched int NOT NULL DEFAULT 0,
            inserted int NOT NULL DEFAULT 0,
            error text
        );
        CREATE INDEX ix_collector_runs_started ON collector_runs (started_at DESC);

        CREATE TABLE llm_calls (
            id bigserial PRIMARY KEY,
            purpose text NOT NULL,
            model text NOT NULL,
            input_tokens int NOT NULL DEFAULT 0,
            output_tokens int NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_llm_calls_created ON llm_calls (created_at);

        CREATE TABLE x_budget (
            month date PRIMARY KEY,
            posts_read int NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        -- Daily usage of quota-limited APIs. scope is e.g. a topic slug ('' = global).
        CREATE TABLE api_quota (
            platform varchar(16) NOT NULL,
            scope text NOT NULL DEFAULT '',
            day date NOT NULL,
            units int NOT NULL DEFAULT 0,
            PRIMARY KEY (platform, scope, day)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS api_quota, x_budget, llm_calls, collector_runs, author_scores,
            edges, post_topics, post_sources, posts, authors, persons, topics CASCADE;
        """
    )
