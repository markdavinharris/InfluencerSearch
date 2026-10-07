import { Badge } from "@/components/ui/badge";

const LABELS: Record<string, string> = {
  x: "X",
  bluesky: "Bluesky",
  reddit: "Reddit",
  youtube: "YouTube",
  news: "News",
  podcast: "Podcast",
};

export function PlatformBadge({ platform }: { platform: string }) {
  return <Badge variant="outline">{LABELS[platform] ?? platform}</Badge>;
}

export const PLATFORM_OPTIONS = Object.entries(LABELS);
