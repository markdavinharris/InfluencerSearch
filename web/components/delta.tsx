import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { cn, fmtDelta } from "@/lib/utils";

export function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="text-xs text-muted-foreground">new</span>;
  const v = Math.round(value * 100);
  const Icon = v > 0 ? ArrowUp : v < 0 ? ArrowDown : Minus;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-xs tabular-nums",
        v > 0 ? "text-good" : v < 0 ? "text-bad" : "text-muted-foreground",
      )}
    >
      <Icon className="size-3" aria-hidden />
      {fmtDelta(value)}
    </span>
  );
}
