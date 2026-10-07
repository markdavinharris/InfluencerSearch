import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { Stats } from "@/lib/types";
import { fmtDate, fmtDateTime, fmtInt } from "@/lib/utils";

function Meter({ used, total, label }: { used: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="tabular-nums text-muted-foreground">
          {fmtInt(used)} / {fmtInt(total)} ({pct.toFixed(0)}%)
        </span>
      </div>
      <div
        className="mt-1 h-2 overflow-hidden rounded-full bg-accent-track"
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default async function StatusPage() {
  await requireAllowedUser();
  let s: Stats;
  try {
    s = await api.stats();
  } catch (e) {
    return (
      <main className="mx-auto max-w-7xl px-4 py-6">
        <ErrorState error={e} />
      </main>
    );
  }
  const byDay = new Map<string, { calls: number; input: number; output: number }>();
  for (const r of s.llm_spend) {
    const d = byDay.get(r.day) ?? { calls: 0, input: 0, output: 0 };
    d.calls += r.calls;
    d.input += r.input_tokens;
    d.output += r.output_tokens;
    byDay.set(r.day, d);
  }
  const errors = s.collector_runs.filter((r) => r.error);
  const daysInMonth = (() => {
    const m = new Date(s.x_budget.month + "T00:00:00Z");
    return new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 0)).getUTCDate();
  })();
  const dayOfMonth = new Date().getUTCDate();
  const onTrack = Math.round((s.x_budget.monthly_budget * dayOfMonth) / daysInMonth);

  return (
    <main className="mx-auto max-w-7xl space-y-4 px-4 py-6">
      <h1 className="text-xl font-semibold">Status</h1>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>X API budget</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Meter used={s.x_budget.posts_read} total={s.x_budget.monthly_budget} label="Posts read this month" />
            <p className="text-xs text-muted-foreground">
              Linear pace for today would be {fmtInt(onTrack)}. Daily allocation per topic:{" "}
              {fmtInt(s.x_budget.daily_allocation_per_topic)} across {s.x_budget.active_topics} active topics.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>LLM tokens today</CardTitle>
          </CardHeader>
          <CardContent>
            <Meter used={s.llm_tokens_today} total={s.llm_daily_token_cap} label="Daily cap" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Totals</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-3 gap-2 text-sm">
              {Object.entries(s.totals).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-muted-foreground">{k.replace("_", " ")}</dt>
                  <dd className="font-medium tabular-nums">{fmtInt(v)}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </div>

      {errors.length > 0 && (
        <Card className="border-bad/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-bad" aria-hidden /> Recent collector errors ({errors.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {errors.slice(0, 10).map((r) => (
                <li key={r.id}>
                  <span className="font-medium">
                    {r.platform} / {r.topic_slug ?? "—"}
                  </span>{" "}
                  <span className="text-muted-foreground">{fmtDateTime(r.started_at)}</span>
                  <div className="break-all text-xs text-muted-foreground">{r.error}</div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Collector runs</CardTitle>
        </CardHeader>
        <CardContent>
          {s.collector_runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No collector runs yet.</p>
          ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Started (ET)</TableHead>
                <TableHead>Platform</TableHead>
                <TableHead>Topic</TableHead>
                <TableHead className="text-right">Fetched</TableHead>
                <TableHead className="text-right">New</TableHead>
                <TableHead>Result</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {s.collector_runs.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="whitespace-nowrap">{fmtDateTime(r.started_at)}</TableCell>
                  <TableCell>{r.platform}</TableCell>
                  <TableCell>{r.topic_slug ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmtInt(r.fetched)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmtInt(r.inserted)}</TableCell>
                  <TableCell>
                    {r.error ? (
                      <Badge variant="bad" title={r.error}>
                        <AlertTriangle className="size-3" aria-hidden /> error
                      </Badge>
                    ) : r.finished_at ? (
                      <Badge variant="good">
                        <CheckCircle2 className="size-3" aria-hidden /> ok
                      </Badge>
                    ) : (
                      <Badge variant="outline">running</Badge>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>LLM usage by day</CardTitle>
        </CardHeader>
        <CardContent>
          {byDay.size === 0 ? (
            <p className="text-sm text-muted-foreground">No LLM calls in the last 30 days.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Day</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Input tokens</TableHead>
                  <TableHead className="text-right">Output tokens</TableHead>
                  <TableHead>Models / purposes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...byDay.entries()].map(([day, v]) => (
                  <TableRow key={day}>
                    <TableCell>{fmtDate(day + "T12:00:00Z")}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtInt(v.calls)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtInt(v.input)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmtInt(v.output)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {s.llm_spend
                        .filter((r) => r.day === day)
                        .map((r) => `${r.model} (${r.purpose})`)
                        .join(", ")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
