import Link from "next/link";

export function Nav({ userSlot }: { userSlot?: React.ReactNode }) {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-12 max-w-7xl items-center gap-6 px-4">
        <Link href="/" className="font-semibold whitespace-nowrap">
          Influence Mapper
        </Link>
        <nav className="flex gap-4 text-sm text-muted-foreground">
          <Link href="/" className="hover:text-foreground">
            Topics
          </Link>
          <Link href="/status" className="hover:text-foreground">
            Status
          </Link>
        </nav>
        <div className="ml-auto hidden sm:block">{userSlot}</div>
      </div>
    </header>
  );
}
