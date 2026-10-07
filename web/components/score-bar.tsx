import { fmtScore } from "@/lib/utils";

/** A 0–1 score as a thin horizontal bar with its value as text (never color alone). */
export function ScoreBar({ value, label, width = 64 }: { value: number; label: string; width?: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="flex items-center gap-2" title={`${label}: ${fmtScore(value)} / 100`}>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-accent-track"
        style={{ width }}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-6 text-right text-xs tabular-nums text-muted-foreground">{fmtScore(value)}</span>
    </div>
  );
}
