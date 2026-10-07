import Link from "next/link";
import { notFound } from "next/navigation";
import { ExternalLink } from "lucide-react";

import { AuthorCell, Avatar } from "@/components/author-cell";
import { ErrorState } from "@/components/error-state";
import { PlatformBadge } from "@/components/platform-badge";
import { ScoreBar } from "@/components/score-bar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { AuthorDetail, EdgeSummary } from "@/lib/types";
import { authorName, cn, fmtDate, fmtInt, fmtScore } from "@/lib/utils";

import { MergeForm } from "./merge-form";

const STANCE: Record<string, "good" | "bad" | "secondary" | "outline"> = {
  support: "good",
  oppose: "bad",
  neutral: "secondary",
  unclear: "outline",
};

export default async function AuthorPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ topic?: string }>;
}) {
  await requireAllowedUser();
  const id = Number((await params).id);
  if (!Number.isInteger(id)) notFound();
  const { topic } = await searchParams;
  let d: AuthorDetail;
  try {
    d = await api.author(id, { topic });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return (
      <main className="mx-auto max-w-7xl px-4 py-6">
        <ErrorState error={e} />
      </main>
    );
  }
  const a = d.author;

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      {topic && (
        <div className="text-sm text-muted-foreground">
          <Link href={`/topics/${topic}`} className="hover:underline">
            ← {d.scores.find((s) => s.topic_slug === topic)?.topic_name ?? topic}
          </Link>
        </div>
      )}
      <header className="mt-2 flex flex-wrap items-start gap-4">
        <Avatar author={a} size={64} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">{authorName(a)}</h1>
            <PlatformBadge platform={a.platform} />
            {d.person && <Badge variant="secondary">person: {d.person.display_name}</Badge>}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            {a.handle && <span>{a.handle}</span>}
            <span>
              <strong className="text-foreground tabular-nums">{fmtInt(a.followers)}</strong> followers
            </span>
            {d.following !== null && (
              <span>
                <strong className="text-foreground tabular-nums">{fmtInt(d.following)}</strong> following
              </span>
            )}
            {a.url && (
              <a href={a.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:underline">
                profile <ExternalLink className="size-3" aria-hidden />
              </a>
            )}
          </div>
          {d.bio && <p className="mt-2 max-w-3xl text-sm">{d.bio}</p>}
        </div>
      </header>

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Scores by topic</CardTitle>
            </CardHeader>
            <CardContent>
              {d.scores.length === 0 ? (
                <p className="text-sm text-muted-foreground">Not ranked on any topic yet.</p>
              ) : (
                <div className="divide-y">
                  {d.scores.map((s) => (
                    <div
                      key={s.topic_slug}
                      className={cn("grid gap-3 py-3 sm:grid-cols-[1fr_auto]", s.topic_slug === topic && "font-medium")}
                    >
                      <div>
                        <Link href={`/topics/${s.topic_slug}`} className="hover:underline">
                          {s.topic_name}
                        </Link>
                        <div className="text-xs text-muted-foreground">
                          #{s.rank} · score {fmtScore(s.composite)} · {s.on_topic_posts} on-topic posts · {fmtDate(s.as_of)}
                        </div>
                      </div>
                      <div className="grid grid-cols-[auto_auto] items-center gap-x-3 gap-y-1 text-xs">
                        <span className="text-muted-foreground">Centrality</span>
                        <ScoreBar value={s.centrality} label="Centrality" width={96} />
                        <span className="text-muted-foreground">Engagement</span>
                        <ScoreBar value={s.engagement} label="Engagement" width={96} />
                        <span className="text-muted-foreground">Reach</span>
                        <ScoreBar value={s.reach} label="Reach" width={96} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent on-topic posts</CardTitle>
            </CardHeader>
            <CardContent>
              {d.recent_posts.length === 0 ? (
                <p className="text-sm text-muted-foreground">No on-topic posts.</p>
              ) : (
                <ul className="divide-y">
                  {d.recent_posts.map((p) => (
                    <li key={`${p.id}-${p.topic_slug}`} className="py-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span>{fmtDate(p.published_at)}</span>
                        <Badge variant="outline">{p.kind}</Badge>
                        {p.stance && <Badge variant={STANCE[p.stance] ?? "outline"}>{p.stance}</Badge>}
                        {!topic && d.scores.length > 1 && <span>{p.topic_slug}</span>}
                        {p.url && (
                          <a href={p.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 hover:underline">
                            open <ExternalLink className="size-3" aria-hidden />
                          </a>
                        )}
                      </div>
                      {p.summary && <p className="mt-1 text-sm font-medium">{p.summary}</p>}
                      <p className="mt-1 line-clamp-4 text-sm whitespace-pre-line text-muted-foreground">{p.text}</p>
                      <div className="mt-1 flex gap-3 text-xs text-muted-foreground tabular-nums">
                        <span>{fmtInt(p.likes)} likes</span>
                        <span>{fmtInt(p.reposts)} reposts</span>
                        <span>{fmtInt(p.quotes)} quotes</span>
                        <span>{fmtInt(p.replies)} replies</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <EdgeList title="Cited by" hint="accounts that quote, repost, reply to or mention them" items={d.cited_by} topic={topic} />
          <EdgeList title="Cites" hint="accounts they quote, repost, reply to or mention" items={d.cites} topic={topic} />
          <Card>
            <CardHeader>
              <CardTitle>Same person on other platforms</CardTitle>
            </CardHeader>
            <CardContent>
              <MergeForm author={a} person={d.person} />
            </CardContent>
          </Card>
        </div>
      </div>
    </main>
  );
}

function EdgeList({ title, hint, items, topic }: { title: string; hint: string; items: EdgeSummary[]; topic?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">None in the current window.</p>
        ) : (
          <ul className="space-y-2">
            {items.map((e) => (
              <li key={e.author.id} className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <AuthorCell author={e.author} topic={topic} />
                </div>
                <span
                  className="text-xs tabular-nums text-muted-foreground"
                  title={Object.entries(e.kinds)
                    .map(([k, n]) => `${n} ${k}`)
                    .join(", ")}
                >
                  {e.count}×
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
