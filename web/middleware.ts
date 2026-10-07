// Every route requires a Clerk session except the sign-in page. Which Google accounts may
// sign in is configured in the Clerk dashboard (Google only, @coldspark.com / allowlist);
// app/layout.tsx re-checks the email domain server-side as defence in depth.
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

const isPublic = createRouteMatcher(["/sign-in(.*)"]);

const clerk = clerkMiddleware(async (auth, req) => {
  if (!isPublic(req)) await auth.protect();
});

const clerkConfigured =
  !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && !!process.env.CLERK_SECRET_KEY;

export default function middleware(req: NextRequest, ev: NextFetchEvent) {
  if (!clerkConfigured) {
    // Local development without Clerk keys: no auth. Anywhere else: refuse to serve.
    if (process.env.NODE_ENV === "development") return NextResponse.next();
    return new NextResponse("Authentication is not configured.", { status: 503 });
  }
  return clerk(req, ev);
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
