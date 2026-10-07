import "server-only";

/** True when Clerk isn't configured and we're in local dev: auth is skipped entirely.
 * In production a missing Clerk config is an error (see middleware.ts). */
export const authDisabled =
  process.env.NODE_ENV === "development" &&
  !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
  !process.env.CLERK_SECRET_KEY;

function list(v: string | undefined): string[] {
  return (v ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export function isAllowedEmail(email: string | null | undefined): boolean {
  if (!email) return false;
  const e = email.toLowerCase();
  const domains = list(process.env.ALLOWED_EMAIL_DOMAINS ?? "coldspark.com");
  const emails = list(process.env.ALLOWED_EMAILS);
  return emails.includes(e) || domains.includes(e.split("@")[1] ?? "");
}

/** The signed-in user's email if they're allowed in, else null. In auth-disabled local dev
 * returns "dev@localhost". Call from pages, layouts and server actions. */
export async function allowedUserEmail(): Promise<string | null> {
  if (authDisabled) return "dev@localhost";
  const { currentUser } = await import("@clerk/nextjs/server");
  const user = await currentUser();
  const email = user?.primaryEmailAddress?.emailAddress ?? null;
  return isAllowedEmail(email) ? email : null;
}

export async function requireAllowedUser(): Promise<string> {
  const email = await allowedUserEmail();
  if (!email) throw new Error("Not authorized");
  return email;
}
