import Link from "next/link";

import { PlatformBadge } from "@/components/platform-badge";
import type { AuthorRef } from "@/lib/types";
import { authorName } from "@/lib/utils";

export function Avatar({ author, size = 28 }: { author: AuthorRef; size?: number }) {
  const name = authorName(author);
  if (author.avatar_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={author.avatar_url}
        alt=""
        width={size}
        height={size}
        className="shrink-0 rounded-full bg-subtle object-cover"
        style={{ width: size, height: size }}
        referrerPolicy="no-referrer"
      />
    );
  }
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full bg-subtle text-xs font-medium text-muted-foreground"
      style={{ width: size, height: size }}
      aria-hidden
    >
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

export function AuthorCell({ author, topic }: { author: AuthorRef; topic?: string }) {
  const href = `/authors/${author.id}${topic ? `?topic=${encodeURIComponent(topic)}` : ""}`;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Avatar author={author} />
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <Link href={href} className="truncate font-medium hover:underline">
            {authorName(author)}
          </Link>
          <PlatformBadge platform={author.platform} />
        </div>
        {author.handle && author.handle !== author.display_name && (
          <div className="truncate text-xs text-muted-foreground">
            {author.platform === "x" || author.platform === "bluesky" ? "@" : ""}
            {author.handle}
          </div>
        )}
      </div>
    </div>
  );
}
