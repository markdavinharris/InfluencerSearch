import Link from "next/link";

import { ErrorState } from "@/components/error-state";
import { PlatformBadge } from "@/components/platform-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { TopicSummary } from "@/lib/types";
import { fmtDate, fmtInt } from "@/lib/utils";

export default async function TopicsPage() {
  await requireAllowedUser();
  let topics: TopicSummary[];
  try {
    topics = await api.topics();
  } catch (e) {
    return (
      <main className="mx-auto max-w-7xl px-4 py-6">
        <ErrorState error={e} />
      </main>
    );
  }
  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="text-xl font-semibold">Topics</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Who drives the conversation on each topic, ranked by citation network, engagement and reach.
      </p>
      {topics.length === 0 && (
        <p className="mt-8 text-sm text-muted-foreground">
          No topics yet. Add a YAML file under <code>topics/</code> and run <code>sync_topics</code>.
        </p>
      )}
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {topics.map((t) => (
          <Link key={t.slug} href={`/topics/${t.slug}`} className="group">
            <Card className="h-full transition-colors group-hover:border-foreground/30">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <CardTitle>{t.name}</CardTitle>
                  {!t.active && <Badge variant="outline">inactive</Badge>}
                </div>
                <CardDescription className="line-clamp-2">{t.description}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <dl className="grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <dt className="text-xs text-muted-foreground">Posts ({t.window_days}d)</dt>
                    <dd className="font-medium tabular-nums">{fmtInt(t.posts_in_window)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">On topic</dt>
                    <dd className="font-medium tabular-nums">{fmtInt(t.on_topic_posts_in_window)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Last scored</dt>
                    <dd className="font-medium">{t.last_scored ? fmtDate(t.last_scored) : "never"}</dd>
                  </div>
                </dl>
                <div>
                  <div className="text-xs text-muted-foreground">Top voices</div>
                  {t.top_authors.length === 0 ? (
                    <div className="text-sm text-muted-foreground">—</div>
                  ) : (
                    <ol className="mt-1 space-y-1 text-sm">
                      {t.top_authors.map((a, i) => (
                        <li key={a.author_id} className="flex items-center gap-2">
                          <span className="w-4 text-xs tabular-nums text-muted-foreground">{i + 1}</span>
                          <span className="truncate">{a.name}</span>
                          <PlatformBadge platform={a.platform} />
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </main>
  );
}
