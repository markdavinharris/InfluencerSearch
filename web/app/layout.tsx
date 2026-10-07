import type { Metadata } from "next";
import { ClerkProvider, SignOutButton, UserButton } from "@clerk/nextjs";

import { Nav } from "@/components/nav";
import { Button } from "@/components/ui/button";
import { allowedUserEmail, authDisabled } from "@/lib/auth";

import "./globals.css";

export const metadata: Metadata = {
  title: "Influence Mapper",
  description: "ColdSpark internal: who matters on a topic",
  robots: { index: false, follow: false },
};

export const dynamic = "force-dynamic";

async function Gate({ children }: { children: React.ReactNode }) {
  const { auth } = await import("@clerk/nextjs/server");
  const { userId } = await auth();
  if (!userId) return <>{children}</>; // only the sign-in page renders signed-out
  const email = await allowedUserEmail();
  if (!email) {
    return (
      <main className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="text-lg font-semibold">Access restricted</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          This tool is limited to ColdSpark Google accounts. Sign out and sign in with your
          @coldspark.com account.
        </p>
        <div className="mt-6">
          <SignOutButton>
            <Button>Sign out</Button>
          </SignOutButton>
        </div>
      </main>
    );
  }
  return (
    <>
      <Nav userSlot={<UserButton />} />
      {children}
    </>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const body = authDisabled ? (
    <>
      <Nav userSlot={<span className="text-xs text-muted-foreground">auth disabled (dev)</span>} />
      {children}
    </>
  ) : (
    <Gate>{children}</Gate>
  );
  const html = (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">{body}</body>
    </html>
  );
  return authDisabled ? html : <ClerkProvider>{html}</ClerkProvider>;
}
