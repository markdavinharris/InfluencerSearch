// Server-only client for the FastAPI backend. Adds the internal key; the browser never calls
// the API directly (BFF pattern).
import "server-only";

import type {
  AuthorDetail,
  AuthorRef,
  InfluencersResponse,
  RisingResponse,
  ScoreHistory,
  SimilarPost,
  Stats,
  TopicSummary,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public path: string,
    body: string,
  ) {
    super(`API ${status} on ${path}: ${body.slice(0, 300)}`);
  }
}

function config() {
  const base = process.env.API_URL;
  const key = process.env.INTERNAL_API_KEY;
  if (!base || !key) throw new Error("API_URL and INTERNAL_API_KEY must be set");
  return { base: base.replace(/\/$/, ""), key };
}

type Query = Record<string, string | number | undefined | null>;

function qs(params?: Query): string {
  if (!params) return "";
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}

async function request<T>(path: string, init?: RequestInit & { query?: Query }): Promise<T> {
  const { base, key } = config();
  const url = `${base}${path}${qs(init?.query)}`;
  const res = await fetch(url, {
    ...init,
    headers: { "X-Internal-Key": key, "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) throw new ApiError(res.status, path, await res.text());
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  topics: () => request<TopicSummary[]>("/topics"),
  influencers: (slug: string, q: { as_of?: string; limit?: number; platform?: string; min_posts?: number }) =>
    request<InfluencersResponse>(`/topics/${encodeURIComponent(slug)}/influencers`, { query: q }),
  rising: (slug: string, q: { days?: number; limit?: number }) =>
    request<RisingResponse>(`/topics/${encodeURIComponent(slug)}/rising`, { query: q }),
  author: (id: number, q: { topic?: string } = {}) =>
    request<AuthorDetail>(`/authors/${id}`, { query: q }),
  searchAuthors: (q: string) => request<AuthorRef[]>("/authors", { query: { q } }),
  scoreHistory: (id: number, topic: string) =>
    request<ScoreHistory>(`/authors/${id}/score-history`, { query: { topic } }),
  similar: (postId: number, topic?: string) =>
    request<SimilarPost[]>(`/posts/${postId}/similar`, { query: { topic } }),
  merge: (author_ids: number[], display_name: string) =>
    request<{ person_id: number; display_name: string; author_ids: number[] }>(
      "/persons/merge",
      { method: "POST", body: JSON.stringify({ author_ids, display_name }) },
    ),
  unmerge: (authorId: number) =>
    request<void>(`/persons/unmerge/${authorId}`, { method: "POST" }),
  stats: () => request<Stats>("/stats"),
};
