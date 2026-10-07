"use client";

import { useState, useTransition } from "react";

import { PlatformBadge } from "@/components/platform-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { AuthorRef } from "@/lib/types";
import { authorName } from "@/lib/utils";

import { mergeAction, searchAuthorsAction, unmergeAction } from "./actions";

export function MergeForm({
  author,
  person,
}: {
  author: AuthorRef;
  person: { id: number; display_name: string; accounts: AuthorRef[] } | null;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<AuthorRef[]>([]);
  const [picked, setPicked] = useState<AuthorRef[]>([]);
  const [name, setName] = useState(person?.display_name ?? authorName(author));
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, start] = useTransition();

  const already = new Set([author.id, ...(person?.accounts.map((a) => a.id) ?? [])]);

  return (
    <div className="space-y-3 text-sm">
      {person && (
        <div>
          <div className="text-xs text-muted-foreground">Linked accounts ({person.display_name})</div>
          <ul className="mt-1 space-y-1">
            {person.accounts.map((a) => (
              <li key={a.id} className="flex items-center gap-2">
                <a href={`/authors/${a.id}`} className="truncate hover:underline">
                  {authorName(a)}
                </a>
                <PlatformBadge platform={a.platform} />
                <button
                  className="ml-auto text-xs text-muted-foreground hover:text-foreground"
                  disabled={pending}
                  onClick={() => start(async () => void (await unmergeAction(a.id)))}
                >
                  unlink
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          start(async () => setResults(await searchAuthorsAction(q)));
        }}
      >
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find this person's other accounts…" />
        <Button type="submit" variant="outline" disabled={pending || q.trim().length < 2}>
          Search
        </Button>
      </form>
      {results.length > 0 && (
        <ul className="max-h-48 space-y-1 overflow-auto rounded-md border p-2">
          {results
            .filter((r) => !already.has(r.id))
            .map((r) => {
              const on = picked.some((p) => p.id === r.id);
              return (
                <li key={r.id}>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => setPicked(on ? picked.filter((p) => p.id !== r.id) : [...picked, r])}
                    />
                    <span className="truncate">{authorName(r)}</span>
                    <PlatformBadge platform={r.platform} />
                  </label>
                </li>
              );
            })}
        </ul>
      )}
      {picked.length > 0 && (
        <div className="space-y-2">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Person name" />
          <Button
            disabled={pending || !name.trim()}
            onClick={() =>
              start(async () => {
                const res = await mergeAction([author.id, ...picked.map((p) => p.id)], name.trim());
                setMsg(`Linked ${res.author_ids.length} accounts as “${res.display_name}”.`);
                setPicked([]);
                setResults([]);
              })
            }
          >
            Link {picked.length + 1} accounts as one person
          </Button>
        </div>
      )}
      {msg && <p className="text-xs text-good">{msg}</p>}
    </div>
  );
}
