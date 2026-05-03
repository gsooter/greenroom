/**
 * Typed API client for the home page composite endpoint.
 *
 * One call returns everything the signed-in home page needs to branch
 * between welcome / personalized / browse layouts. Client-only — the
 * payload is per-user and must never land in a shared SSR cache.
 */

import { fetchJson } from "@/lib/api/client";
import type { Envelope, HomePayload } from "@/types";

export async function getHome(token: string): Promise<HomePayload> {
  const res = await fetchJson<Envelope<HomePayload>>("/api/v1/me/home", {
    token,
  });
  return res.data;
}
