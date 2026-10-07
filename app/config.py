"""Application settings, read from environment / .env via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/influence"
    internal_api_key: str = ""

    # LLM (classification) — any OpenAI-compatible endpoint
    llm_base_url: str = "https://api.anthropic.com/v1"
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-4-5"
    llm_daily_token_cap: int = 2_000_000
    llm_batch_size: int = 20

    # Embeddings — any OpenAI-compatible endpoint
    embed_base_url: str = "https://api.openai.com/v1"
    embed_api_key: str = ""
    embed_model: str = "text-embedding-3-small"
    embed_dims: int = 1536

    # X
    x_bearer_token: str = ""
    x_monthly_post_budget: int = 8000
    x_include_retweets: bool = True

    # Bluesky
    bluesky_handle: str = ""
    bluesky_app_password: str = ""

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "coldspark-influence-mapper/0.1"

    # YouTube
    youtube_api_key: str = ""
    youtube_daily_search_cap: int = 30

    # Podcasts
    podcastindex_key: str = ""
    podcastindex_secret: str = ""

    # News
    news_rss_feeds: str = ""  # comma-separated feed URLs, applied to all topics

    # Which collectors run (comma-separated). A failing collector never blocks others.
    enabled_collectors: str = "x,bluesky,reddit,youtube,news,podcasts"

    # Scoring
    on_topic_min_confidence: float = 0.6
    score_w_centrality: float = 0.5
    score_w_engagement: float = 0.3
    score_w_reach: float = 0.2

    @property
    def enabled_collector_set(self) -> set[str]:
        return {c.strip() for c in self.enabled_collectors.split(",") if c.strip()}

    @property
    def rss_feed_list(self) -> list[str]:
        return [u.strip() for u in self.news_rss_feeds.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
