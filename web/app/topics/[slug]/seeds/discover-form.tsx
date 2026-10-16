"use client";

import { useRouter } from "next/navigation";
import { useActionState, useEffect } from "react";

import { Button } from "@/components/ui/button";

import { startDiscoveryAction } from "./actions";

const SOURCES = [
  { value: "existing", label: "Already collected", hint: "free" },
  { value: "bluesky", label: "Bluesky", hint: "free" },
  { value: "x", label: "X", hint: "uses ~100 reads of the X budget" },
  { value: "youtube", label: "YouTube", hint: "1 of the daily searches" },
  { value: "podcasts", label: "Podcasts", hint: "free" },
];

export function DiscoverForm({ slug, running }: { slug: string; running: boolean }) {
  const [state, action, pending] = useActionState(startDiscoveryAction, null);
  const router = useRouter();

  // While a search runs in the background, refresh every few seconds to show results.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => router.refresh(), 4000);
    return () => clearInterval(t);
  }, [running, router]);

  return (
    <form action={action} className="space-y-3 text-sm">
      <input type="hidden" name="slug" value={slug} />
      <fieldset className="flex flex-wrap gap-x-4 gap-y-2">
        <legend className="sr-only">Where to look</legend>
        {SOURCES.map((s) => (
          <label key={s.value} className="flex items-center gap-1.5">
            <input type="checkbox" name="sources" value={s.value} defaultChecked={s.value !== "x"} />
            <span>{s.label}</span>
            <span className="text-xs text-muted-foreground">({s.hint})</span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5">
          <span className="text-muted-foreground">Min followers</span>
          <input
            type="number"
            name="min_followers"
            min={0}
            step={100}
            defaultValue={500}
            className="h-8 w-24 rounded-md border bg-card px-2"
          />
        </label>
        <Button type="submit" disabled={pending || running}>
          {running ? "Searching…" : pending ? "Starting…" : "Find candidates"}
        </Button>
        {state?.error && <span className="text-bad">{state.error}</span>}
      </div>
    </form>
  );
}
