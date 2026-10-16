import Link from "next/link";
import { notFound } from "next/navigation";
import { AlertTriangle, Check, ExternalLink, Undo2, X as XIcon } from "lucide-react";

import { Avatar } from "@/components/author-cell";
import { ErrorState } from "@/components/error-state";
import { PLATFORM_OPTIONS, PlatformBadge } from "@/components/platform-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { DiscoverStatus, SeedCandidate, SeedStatus } from "@/lib/types";
import { cn, fmtDateTime, fmtInt } from "@/lib/utils";

import { decideSeedAction } from "./actions";
import { DiscoverForm } from "./discover-form";

type SP = { status?: string; platform?: string; min_followers?: string };

const SOURCE_LABEL: Record<string, string> = {
  existing: "already collected",
  bluesky: "Bluesky search",
  x: "X search",
  youtube: "YouTube search",
  podcasts: "Podcast Index",
};

function DecideButton({
  slug,
  id,
  status,
  label,
  icon,
  variant = "outline",
}: {
  slug: string;
  id: number;
  status: SeedStatus;
  label: string;
  icon: React.ReactNode;
  variant?: "default" | "outline" | "ghost";
}) {
  return (
    <form action={decideSeedAction}>
      <input type="hidden" name="slug" value={slug} />
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="status" value={status} />
      <Button type="submit" size="sm" variant={variant}>
        {icon}
        {label}
      </Button>
    </form>
  );
}

function CandidateRow({ c, slug }: { c: SeedCandidate; slug: string }) {
  const name = c.display_name || c.seed_handle;
  return (
    <TableRow className="align-top">
      <TableCell className="max-w-72">
        <div className="flex items-start gap-2">
          <Avatar
            author={{ id: c.id, platform: c.platform, handle: c.seed_handle, display_name: name, avatar_url: c.avatar_url, url: c.url, followers: c.followers, person_id: null }}
          />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              {c.url ? (
                <a href={c.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">
                  {name}
                </a>
              ) : (
                <span className="font-medium">{name}</span>
              )}
              <PlatformBadge platform={c.platform} />
            </div>
            <div className="truncate text-xs text-muted-foreground">{c.seed_handle}</div>
            {c.bio && <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">{c.bio}</p>}
          </div>
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums">{c.platform === "podcast" ? "—" : fmtInt(c.followers)}</TableCell>
      <TableCell className="text-right tabular-nums">{c.matched_posts}</TableCell>
      <TableCell className="max-w-48">
        <div className="flex flex-wrap gap-1">
          {c.bio_match && (
            <Badge variant="good" title="Their bio or channel description mentions a topic keyword">
              <Check className="size-3" aria-hidden /> bio
            </Badge>
          )}
          {c.matched_keywords.map((k) => (
            <Badge key={k} variant="secondary">
              {k}
            </Badge>
          ))}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">{c.sources.map((s) => SOURCE_LABEL[s] ?? s).join(", ")}</div>
      </TableCell>
      <TableCell className="max-w-96">
        {c.sample_text ? (
          <p className="line-clamp-3 text-xs">
            {c.sample_text}{" "}
            {c.sample_url && (
              <a href={c.sample_url} target="_blank" rel="noreferrer" className="inline-flex items-center text-muted-foreground hover:underline">
                <ExternalLink className="size-3" aria-label="open post" />
              </a>
            )}
          </p>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex justify-end gap-1.5">
          {c.status === "pending" ? (
            <>
              <DecideButton slug={slug} id={c.id} status="accepted" label="Accept" variant="default" icon={<Check className="size-3.5" aria-hidden />} />
              <DecideButton slug={slug} id={c.id} status="rejected" label="Reject" icon={<XIcon className="size-3.5" aria-hidden />} />
            </>
          ) : (
            <DecideButton slug={slug} id={c.id} status="pending" label="Restore" variant="ghost" icon={<Undo2 className="size-3.5" aria-hidden />} />
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

function RunSummary({ st }: { st: DiscoverStatus }) {
  if (!st.last_started) return <p className="text-xs text-muted-foreground">No searches yet.</p>;
  // the latest search = runs that started within a few minutes of the newest one
  const latest = new Date(st.last_started).getTime();
  const runs = st.runs.filter((r) => latest - new Date(r.started_at).getTime() < 10 * 60_000);
  return (
    <div className="space-y-1 text-xs text-muted-foreground">
      <div>
        {st.running ? "Searching now — results appear as each source finishes." : `Last search ${fmtDateTime(st.last_started)} ET`}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {runs.map((r) => (
          <li key={r.id} className={cn("inline-flex items-center gap-1", r.error && "text-bad")} title={r.error ?? undefined}>
            {r.error && <AlertTriangle className="size-3" aria-hidden />}
            {SOURCE_LABEL[r.platform.replace("seeds:", "")] ?? r.platform}:{" "}
            {r.error ? "failed" : r.finished_at ? `${fmtInt(r.fetched)} looked at` : "running"}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default async function SeedsPage({ params, searchParams }: { params: Promise<{ slug: string }>; searchParams: Promise<SP> }) {
  await requireAllowedUser();
  const { slug } = await params;
  const sp = await searchParams;
  const status: SeedStatus = sp.status === "rejected" ? "rejected" : "pending";

  let seeds, cands, discover, topicName = slug;
  try {
    [seeds, cands, discover] = await Promise.all([
      api.topicSeeds(slug),
      api.seedCandidates(slug, {
        status,
        platform: sp.platform || undefined,
        min_followers: sp.min_followers ? Number(sp.min_followers) : undefined,
      }),
      api.discoverStatus(slug),
    ]);
    topicName = (await api.topics()).find((t) => t.slug === slug)?.name ?? slug;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return (
      <main className="mx-auto max-w-7xl px-4 py-6">
        <ErrorState error={e} />
      </main>
    );
  }

  const tabHref = (s: SeedStatus) => {
    const q = new URLSearchParams();
    if (s !== "pending") q.set("status", s);
    if (sp.platform) q.set("platform", sp.platform);
    if (sp.min_followers) q.set("min_followers", sp.min_followers);
    const qs = q.toString();
    return `/topics/${slug}/seeds${qs ? `?${qs}` : ""}`;
  };

  return (
    <main className="mx-auto max-w-7xl space-y-4 px-4 py-6">
      <div>
        <div className="text-sm text-muted-foreground">
          <Link href="/" className="hover:underline">
            Topics
          </Link>{" "}
          /{" "}
          <Link href={`/topics/${slug}`} className="hover:underline">
            {topicName}
          </Link>{" "}
          /
        </div>
        <h1 className="mt-1 text-xl font-semibold">Seed accounts</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Seeds are accounts whose posts are always collected for this topic, even when they don’t use the search keywords.
          Being a seed doesn’t raise anyone’s ranking. Search for candidates below, then accept the ones worth following.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <Card>
          <CardHeader>
            <CardTitle>Find candidates</CardTitle>
            <CardDescription>
              Looks for accounts posting about the topic’s keywords, or describing themselves with them.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <DiscoverForm slug={slug} running={discover.running} />
            <RunSummary st={discover} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Current seeds ({seeds.from_config.length + seeds.accepted.length})</CardTitle>
          </CardHeader>
          <CardContent>
            {seeds.from_config.length + seeds.accepted.length === 0 ? (
              <p className="text-sm text-muted-foreground">None yet.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {seeds.accepted.map((c) => (
                  <li key={c.id} className="flex items-center gap-2">
                    <PlatformBadge platform={c.platform} />
                    <span className="min-w-0 truncate">{c.display_name || c.seed_handle}</span>
                    <span className="hidden min-w-0 truncate text-xs text-muted-foreground sm:inline">
                      {c.decided_by && `added by ${c.decided_by}`}
                    </span>
                    <div className="ml-auto">
                      <DecideButton slug={slug} id={c.id} status="pending" label="Remove" variant="ghost" icon={<Undo2 className="size-3.5" aria-hidden />} />
                    </div>
                  </li>
                ))}
                {seeds.from_config.map((s) => {
                  const [platform, ...rest] = s.split(":");
                  return (
                    <li key={s} className="flex items-center gap-2">
                      <PlatformBadge platform={platform} />
                      <span className="min-w-0 truncate">{rest.join(":")}</span>
                      <span className="ml-auto text-xs text-muted-foreground" title={`Edit topics/${slug}.yaml to change`}>
                        in topic file
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-md border bg-card p-0.5 text-sm" role="tablist">
          {(["pending", "rejected"] as const).map((s) => (
            <Link
              key={s}
              role="tab"
              aria-selected={status === s}
              href={tabHref(s)}
              className={cn("rounded px-3 py-1", status === s ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}
            >
              {s === "pending" ? "To review" : "Rejected"} ({cands.counts[s]})
            </Link>
          ))}
        </div>
        <form action={`/topics/${slug}/seeds`} className="flex flex-wrap items-center gap-2 text-sm">
          {status !== "pending" && <input type="hidden" name="status" value={status} />}
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">Platform</span>
            <select name="platform" defaultValue={sp.platform ?? ""} className="h-8 rounded-md border bg-card px-2">
              <option value="">All</option>
              {PLATFORM_OPTIONS.filter(([v]) => ["x", "bluesky", "youtube", "podcast"].includes(v)).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">Min followers</span>
            <input type="number" name="min_followers" min={0} step={100} defaultValue={sp.min_followers ?? ""} className="h-8 w-24 rounded-md border bg-card px-2" />
          </label>
          <button type="submit" className={buttonVariants({ variant: "outline", size: "sm" })}>
            Apply
          </button>
        </form>
      </div>

      <div className="rounded-lg border bg-card">
        {cands.rows.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">
            {status === "pending" ? "Nothing to review. Run a search above." : "Nothing rejected."}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Account</TableHead>
                <TableHead className="text-right">Followers</TableHead>
                <TableHead className="text-right" title="Recent posts matching the topic keywords (or on-topic posts already collected)">
                  Matching posts
                </TableHead>
                <TableHead>Why it showed up</TableHead>
                <TableHead>Example</TableHead>
                <TableHead className="sr-only">Decision</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cands.rows.map((c) => (
                <CandidateRow key={c.id} c={c} slug={slug} />
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </main>
  );
}
