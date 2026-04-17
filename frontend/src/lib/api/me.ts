/**
 * API client for the authenticated /me endpoints.
 *
 * Every function here requires a JWT — pass the one from AuthContext
 * (`useAuth().token`). Server components should not import this
 * module: /me is private and cannot be SSR'd against a shared cache.
 */

import { fetchJson } from "@/lib/api/client";
import type { Envelope, User, UserPatch } from "@/types";

export async function getMe(token: string): Promise<User> {
  const res = await fetchJson<Envelope<User>>("/api/v1/me", { token });
  return res.data;
}

export async function updateMe(token: string, patch: UserPatch): Promise<User> {
  const res = await fetchJson<Envelope<User>>("/api/v1/me", {
    method: "PATCH",
    token,
    body: patch,
  });
  return res.data;
}

export async function deleteMe(token: string): Promise<void> {
  await fetchJson<void>("/api/v1/me", { method: "DELETE", token });
}
