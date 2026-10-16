import Link from "next/link";
import { notFound } from "next/navigation";

import { AuthorCell } from "@/components/author-cell";
import { Delta } from "@/components/delta";
import { ErrorState } from "@/components/error-state";
import { PLATFORM_OPTIONS } from "@/components/platform-badge";
import { ScoreBar } from "@/components/score-bar";
import { Sparkline } from "@/components/sparkline";
import { buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { InfluencerRow } from "@/lib/types";
import { cn, fmtDate, fmtInt, fmtScore } from "@/lib/utils";

type SP = Record<string, string | undefined>;

const SORTS: Record<string, { label: string; key: (r: InfluencerRow) => number; title?: string }> = {
  rank: { label: "Rank", key: (r) => -r.rank },
  composite: { label: "Score", key: (r) => r.composite, title: "50% centrality + 30% engagement + 20% reach" },
  centrality: { label: "Centrality", key: (r) => r.centrality, title: "How much others on this topic quote, repost, reply to and mention them (PageRank percentile)" },
  engagement: { label: "Engagement", key: (r) => r.engagement, title: "Likes, reposts, quotes and replies on their on-topic posts (percentile)" },
  reach: { label: "Reach", key: (r) => r.reach, title: "Follower/subscriber count (percentile)" },
  followers: { label: "Followers", key: (r) => r.author.followers ?? -1 },
  posts: { label: "Posts", key: (r) => r.on_topic_posts, title: "On-topic posts in the window" },
  delta: { label: "7d", key: (r) => r.delta_7d ?? -Infinity, title: "Change in score vs. 7 days ago" },
};

function href(slug: string, sp: SP, patch: SP) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...sp, ...patch })) if (v) q.set(k, v);
  const s = q.toString();
  return `/topics/${slug}${s ? `?${s}` : ""}`;
}

export default async function TopicPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SP>;
}) {
  await requireAllowedUser();
  const { slug } = await params;
  const sp = await searchParams;
  const view = sp.view === "rising" ? "rising" : "influencers";

  const topics = await api.topics().catch(() => []);
  const topic = topics.find((t) => t.slug === slug);
  if (topics.length && !topic) notFound();

  const tabs = (
    <div className="inline-flex rounded-md border bg-card p-0.5 text-sm" role="tablist">
      {(["influencers", "rising"] as const).map((v) => (
        <Link
          key={v}
          role="tab"
          aria-selected={view === v}
          href={href(slug, sp, { view: v === "rising" ? "rising" : undefined })}
          className={cn(
            "rounded px-3 py-1",
            view === v ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {v === "influencers" ? "Influencers" : "Rising"}
        </Link>
      ))}
    </div>
  );

  return (
    <main className="mx-auto max-w-7xl px-4 py-6">
      <div className="text-sm text-muted-foreground">
        <Link href="/" className="hover:underline">
          Topics
        </Link>{" "}
        /
      </div>
      <h1 className="mt-1 text-xl font-semibold">{topic?.name ?? slug}</h1>
      {topic && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{topic.description}</p>}
      <div className="mt-5 flex flex-wrap items-center gap-3">
        {tabs}
        {view === "influencers" && <Filters slug={slug} sp={sp} />}
        <Link href={`/topics/${slug}/seeds`} className={cn(buttonVariants({ variant: "outline", size: "sm" }), "ml-auto")}>
          Seed accounts
        </Link>
      </div>
      <div className="mt-4">
        {view === "rising" ? <Rising slug={slug} /> : <Influencers slug={slug} sp={sp} />}
      </div>
    </main>
  );
}

function Filters({ slug, sp }: { slug: string; sp: SP }) {
  return (
    <form action={`/topics/${slug}`} className="flex flex-wrap items-center gap-2 text-sm">
      {sp.sort && <input type="hidden" name="sort" value={sp.sort} />}
      <label className="flex items-center gap-1.5">
        <span className="text-muted-foreground">Platform</span>
        <select name="platform" defaultValue={sp.platform ?? ""} className="h-8 rounded-md border bg-card px-2">
          <option value="">All</option>
          {PLATFORM_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        <span className="text-muted-foreground">Min posts</span>
        <input
          type="number"
          name="min_posts"
          min={0}
          defaultValue={sp.min_posts ?? ""}
          className="h-8 w-16 rounded-md border bg-card px-2"
        />
      </label>
      <button type="submit" className={buttonVariants({ variant: "outline", size: "sm" })}>
        Apply
      </button>
    </form>
  );
}

async function Influencers({ slug, sp }: { slug: string; sp: SP }) {
  let data;
  try {
    data = await api.influencers(slug, {
      platform: sp.platform,
      min_posts: sp.min_posts ? Number(sp.min_posts) : undefined,
      limit: 100,
    });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return <ErrorState error={e} />;
  }
  if (!data.as_of) {
    return <p className="text-sm text-muted-foreground">This topic hasn’t been scored yet.</p>;
  }
  const sortKey = sp.sort && SORTS[sp.sort] ? sp.sort : "rank";
  const rows = [...data.rows].sort((a, b) => SORTS[sortKey].key(b) - SORTS[sortKey].key(a));

  const Th = ({ k, className }: { k: string; className?: string }) => (
    <TableHead className={className} title={SORTS[k].title}>
      <Link
        href={href(slug, sp, { sort: k === "rank" ? undefined : k })}
        className={cn("hover:text-foreground", sortKey === k && "text-foreground underline underline-offset-4")}
      >
        {SORTS[k].label}
      </Link>
    </TableHead>
  );

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2 text-xs text-muted-foreground">
        <span>
          Snapshot {fmtDate(data.as_of)} · {rows.length} accounts
        </span>
        <span className="hidden sm:inline">Score = 50% centrality + 30% engagement + 20% reach</span>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <Th k="rank" className="w-12" />
            <TableHead>Account</TableHead>
            <Th k="composite" />
            <TableHead>30 days</TableHead>
            <Th k="centrality" />
            <Th k="engagement" />
            <Th k="reach" />
            <Th k="followers" className="text-right" />
            <Th k="posts" className="text-right" />
            <Th k="delta" className="text-right" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.author.id}>
              <TableCell className="tabular-nums text-muted-foreground">{r.rank}</TableCell>
              <TableCell className="max-w-72">
                <AuthorCell author={r.author} topic={slug} />
              </TableCell>
              <TableCell className="text-base font-semibold tabular-nums">{fmtScore(r.composite)}</TableCell>
              <TableCell>
                <Sparkline values={r.sparkline} />
              </TableCell>
              <TableCell>
                <ScoreBar value={r.centrality} label="Centrality" />
              </TableCell>
              <TableCell>
                <ScoreBar value={r.engagement} label="Engagement" />
              </TableCell>
              <TableCell>
                <ScoreBar value={r.reach} label="Reach" />
              </TableCell>
              <TableCell className="text-right tabular-nums">{fmtInt(r.author.followers)}</TableCell>
              <TableCell className="text-right tabular-nums">{r.on_topic_posts}</TableCell>
              <TableCell className="text-right">
                <Delta value={r.delta_7d} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

async function Rising({ slug }: { slug: string }) {
  let data;
  try {
    data = await api.rising(slug, { days: 7, limit: 50 });
  } catch (e) {
    return <ErrorState error={e} />;
  }
  if (!data.compared_to) {
    return (
      <p className="text-sm text-muted-foreground">
        Rising needs at least a week of daily snapshots to compare against.
      </p>
    );
  }
  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b px-3 py-2 text-xs text-muted-foreground">
        Biggest score gains, {fmtDate(data.compared_to)} → {data.as_of && fmtDate(data.as_of)}
      </div>
      {data.rows.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">Nobody gained ground this week.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Account</TableHead>
              <TableHead className="text-right">Change</TableHead>
              <TableHead className="text-right">Was</TableHead>
              <TableHead className="text-right">Now</TableHead>
              <TableHead className="text-right">Rank now</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.rows.map((r) => (
              <TableRow key={r.author.id}>
                <TableCell>
                  <AuthorCell author={r.author} topic={slug} />
                </TableCell>
                <TableCell className="text-right">
                  <Delta value={r.delta} />
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {r.previous_composite === null ? "new" : fmtScore(r.previous_composite)}
                </TableCell>
                <TableCell className="text-right font-semibold tabular-nums">{fmtScore(r.composite)}</TableCell>
                <TableCell className="text-right tabular-nums">{r.rank}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
