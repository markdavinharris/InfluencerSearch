"use server";

import { revalidatePath } from "next/cache";

import { api } from "@/lib/api";
import { requireAllowedUser } from "@/lib/auth";
import type { AuthorRef } from "@/lib/types";

export async function searchAuthorsAction(q: string): Promise<AuthorRef[]> {
  await requireAllowedUser();
  if (q.trim().length < 2) return [];
  return api.searchAuthors(q.trim());
}

export async function mergeAction(authorIds: number[], displayName: string) {
  await requireAllowedUser();
  const res = await api.merge(authorIds, displayName);
  for (const id of res.author_ids) revalidatePath(`/authors/${id}`);
  return res;
}

export async function unmergeAction(authorId: number) {
  await requireAllowedUser();
  await api.unmerge(authorId);
  revalidatePath(`/authors/${authorId}`);
}
