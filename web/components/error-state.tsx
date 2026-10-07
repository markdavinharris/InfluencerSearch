import { AlertTriangle } from "lucide-react";

export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-bad/40 bg-bad/5 p-4 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
      <div>
        <div className="font-medium">Couldn’t load data from the API</div>
        <div className="mt-1 break-all text-muted-foreground">{msg}</div>
      </div>
    </div>
  );
}
