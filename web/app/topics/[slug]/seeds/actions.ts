"use server";

import { revalidatePath } from "next/cache";

import { api, ApiError } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { SeedSource, SeedStatus } from "@/lib/types";

const STATUSES: SeedStatus[] = ["pending", "accepted", "rejected"];
const SOURCES: SeedSource[] = ["existing", "bluesky", "x", "youtube", "podcasts"];

export async function decideSeedAction(formData: FormData) {
  const email = await requireAllowedUser();
  const id = Number(formData.get("id"));
  const status = String(formData.get("status")) as SeedStatus;
  const slug = String(formData.get("slug"));
  if (!Number.isInteger(id) || !STATUSES.includes(status)) throw new Error("bad request");
  await api.decideSeed(id, status, email);
  revalidatePath(`/topics/${slug}/seeds`);
}

export async function startDiscoveryAction(
  _prev: { error?: string; ok?: boolean } | null,
  formData: FormData,
): Promise<{ error?: string; ok?: boolean }> {
  await requireAllowedUser();
  const slug = String(formData.get("slug"));
  const sources = formData.getAll("sources").map(String).filter((s): s is SeedSource => SOURCES.includes(s as SeedSource));
  const minFollowers = Number(formData.get("min_followers") || 0);
  if (sources.length === 0) return { error: "Pick at least one source." };
  try {
    await api.startDiscovery(slug, { sources, min_followers: Math.max(0, minFollowers) });
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return { error: "A search is already running for this topic." };
    throw e;
  }
  revalidatePath(`/topics/${slug}/seeds`);
  return { ok: true };
}
