"""seed candidates: discovered accounts a human accepts or rejects as topic seeds

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE seed_candidates (
            id serial PRIMARY KEY,
            topic_id int NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            platform varchar(16) NOT NULL,
            platform_id text NOT NULL,
            -- what goes after "platform:" in seed_accounts (handle, channel id, feed URL)
            seed_handle text NOT NULL,
            display_name text,
            bio text,
            url text,
            avatar_url text,
            followers int,
            matched_posts int NOT NULL DEFAULT 0,
            matched_keywords text[] NOT NULL DEFAULT '{}',
            bio_match boolean NOT NULL DEFAULT false,
            sample_text text,
            sample_url text,
            sources text[] NOT NULL DEFAULT '{}',
            score real NOT NULL DEFAULT 0,
            status varchar(10) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected')),
            decided_at timestamptz,
            decided_by text,
            first_seen timestamptz NOT NULL DEFAULT now(),
            last_seen timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_seed_candidates UNIQUE (topic_id, platform, platform_id)
        );
        CREATE INDEX ix_seed_candidates_topic_status_score
            ON seed_candidates (topic_id, status, score DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS seed_candidates")
