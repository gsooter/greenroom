/**
 * API client for venue comments — list, submit, vote, delete.
 *
 * All four endpoints live under `/api/v1/venues/<slug>/comments`.
 *
 * Auth is optional on list and vote (signed-out callers pass a
 * `session_id` so their vote can be deduped), required on submit and
 * delete.
 */

import { fetchJson } from "@/lib/api/client";
import type {
  Envelope,
  VenueComment,
  VenueCommentCategory,
  VenueCommentSort,
  VenueCommentVoteResult,
  VenueCommentsResponse,
} from "@/types";

export interface ListCommentsParams {
  category?: VenueCommentCategory;
  sort?: VenueCommentSort;
  limit?: number;
  sessionId?: string;
}

export async function listVenueComments(
  slug: string,
  token: string | null,
  { category, sort, limit, sessionId }: ListCommentsParams = {},
): Promise<VenueCommentsResponse> {
  return fetchJson<VenueCommentsResponse>(
    `/api/v1/venues/${slug}/comments`,
    {
      token: token ?? undefined,
      query: {
        category,
        sort,
        limit,
        // Only tag the request with a guest session when signed out.
        session_id: token ? undefined : sessionId,
      },
      revalidateSeconds: 0,
    },
  );
}

export interface SubmitCommentInput {
  category: VenueCommentCategory;
  body: string;
  /** Honeypot field — must be empty. Bots auto-fill every form field. */
  honeypot?: string;
}

export async function submitVenueComment(
  slug: string,
  token: string,
  input: SubmitCommentInput,
): Promise<VenueComment> {
  const res = await fetchJson<Envelope<VenueComment>>(
    `/api/v1/venues/${slug}/comments`,
    {
      method: "POST",
      token,
      body: {
        category: input.category,
        body: input.body,
        honeypot: input.honeypot ?? "",
      },
    },
  );
  return res.data;
}

export async function voteOnVenueComment(
  slug: string,
  commentId: string,
  token: string | null,
  value: -1 | 0 | 1,
  sessionId: string | null,
): Promise<VenueCommentVoteResult> {
  const res = await fetchJson<Envelope<VenueCommentVoteResult>>(
    `/api/v1/venues/${slug}/comments/${commentId}/vote`,
    {
      method: "POST",
      token: token ?? undefined,
      body: {
        value,
        session_id: token ? undefined : sessionId,
      },
    },
  );
  return res.data;
}

export async function deleteVenueComment(
  slug: string,
  commentId: string,
  token: string,
): Promise<void> {
  await fetchJson<void>(
    `/api/v1/venues/${slug}/comments/${commentId}`,
    { method: "DELETE", token },
  );
}
