// Mirrors app/schemas.py response models.

export type Platform = "x" | "bluesky" | "reddit" | "youtube" | "news" | "podcast";

export interface AuthorRef {
  id: number;
  platform: Platform;
  handle: string | null;
  display_name: string | null;
  avatar_url: string | null;
  url: string | null;
  followers: number | null;
  person_id: number | null;
}

export interface TopicSummary {
  id: number;
  slug: string;
  name: string;
  description: string;
  active: boolean;
  window_days: number;
  posts_in_window: number;
  on_topic_posts_in_window: number;
  last_scored: string | null;
  top_authors: { author_id: number; name: string; platform: Platform }[];
}

export interface InfluencerRow {
  rank: number;
  author: AuthorRef;
  composite: number;
  centrality: number;
  engagement: number;
  reach: number;
  on_topic_posts: number;
  delta_7d: number | null;
  sparkline: number[];
}

export interface InfluencersResponse {
  topic: string;
  as_of: string | null;
  rows: InfluencerRow[];
}

export interface RisingRow {
  author: AuthorRef;
  rank: number;
  composite: number;
  previous_composite: number | null;
  delta: number;
}

export interface RisingResponse {
  topic: string;
  as_of: string | null;
  compared_to: string | null;
  rows: RisingRow[];
}

export interface AuthorTopicScore {
  topic_slug: string;
  topic_name: string;
  as_of: string;
  rank: number;
  composite: number;
  centrality: number;
  engagement: number;
  reach: number;
  on_topic_posts: number;
}

export interface AuthorPost {
  id: number;
  topic_slug: string;
  text: string;
  url: string | null;
  published_at: string;
  kind: string;
  likes: number;
  reposts: number;
  replies: number;
  quotes: number;
  stance: string | null;
  summary: string | null;
  confidence: number;
}

export interface EdgeSummary {
  author: AuthorRef;
  count: number;
  kinds: Record<string, number>;
}

export interface AuthorDetail {
  author: AuthorRef;
  bio: string | null;
  following: number | null;
  first_seen: string;
  last_seen: string;
  person: { id: number; display_name: string; accounts: AuthorRef[] } | null;
  scores: AuthorTopicScore[];
  recent_posts: AuthorPost[];
  cited_by: EdgeSummary[];
  cites: EdgeSummary[];
}

export interface ScoreHistory {
  author_id: number;
  topic: string;
  points: {
    as_of: string;
    composite: number;
    centrality: number;
    engagement: number;
    reach: number;
    rank: number;
  }[];
}

export interface SimilarPost {
  id: number;
  author: AuthorRef;
  text: string;
  url: string | null;
  published_at: string;
  summary: string | null;
  similarity: number;
}

export interface Stats {
  collector_runs: {
    id: number;
    topic_slug: string | null;
    platform: string;
    started_at: string;
    finished_at: string | null;
    fetched: number;
    inserted: number;
    error: string | null;
  }[];
  llm_spend: {
    day: string;
    model: string;
    purpose: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
  }[];
  llm_tokens_today: number;
  llm_daily_token_cap: number;
  x_budget: {
    month: string;
    monthly_budget: number;
    posts_read: number;
    remaining: number;
    daily_allocation_per_topic: number;
    active_topics: number;
  };
  totals: Record<string, number>;
}

export type SeedStatus = "pending" | "accepted" | "rejected";
export type SeedSource = "existing" | "bluesky" | "x" | "youtube" | "podcasts";

export interface SeedCandidate {
  id: number;
  platform: Platform;
  seed_handle: string;
  display_name: string | null;
  bio: string | null;
  url: string | null;
  avatar_url: string | null;
  followers: number | null;
  matched_posts: number;
  matched_keywords: string[];
  bio_match: boolean;
  sample_text: string | null;
  sample_url: string | null;
  sources: string[];
  score: number;
  status: SeedStatus;
  decided_by: string | null;
  decided_at: string | null;
  last_seen: string;
}

export interface SeedCandidatesResponse {
  topic: string;
  counts: Record<SeedStatus, number>;
  rows: SeedCandidate[];
}

export interface TopicSeeds {
  topic: string;
  from_config: string[];
  accepted: SeedCandidate[];
}

export interface DiscoverStatus {
  running: boolean;
  last_started: string | null;
  last_finished: string | null;
  runs: Stats["collector_runs"];
}
