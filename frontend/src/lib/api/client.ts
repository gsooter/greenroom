/**
 * Shared fetch helper for the Greenroom backend API.
 *
 * Server components call these functions directly (no TanStack Query)
 * so SSR and AI crawlers get fully-rendered HTML. Client components
 * that need the same data should wrap these with TanStack Query.
 */

import { config } from "@/lib/config";
import type { ApiError } from "@/types";

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "ApiRequestError";
  }
}

export class ApiNotFoundError extends ApiRequestError {
  constructor(code: string, message: string) {
    super(404, code, message);
    this.name = "ApiNotFoundError";
  }
}

export interface FetchJsonOptions extends Omit<RequestInit, "body"> {
  query?: Record<string, string | number | boolean | string[] | undefined>;
  revalidateSeconds?: number;
  token?: string | null;
  body?: unknown;
}

function buildUrl(path: string, query?: FetchJsonOptions["query"]): string {
  const base = config.apiUrl.replace(/\/$/, "");
  const url = new URL(`${base}${path}`);
  if (!query) return url.toString();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, String(item));
    } else {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function fetchJson<T>(
  path: string,
  {
    query,
    revalidateSeconds = 60,
    token,
    body,
    method,
    headers,
    ...init
  }: FetchJsonOptions = {},
): Promise<T> {
  const url = buildUrl(path, query);
  const requestHeaders: Record<string, string> = {
    Accept: "application/json",
    ...(headers as Record<string, string> | undefined),
  };
  if (token) requestHeaders.Authorization = `Bearer ${token}`;
  if (body !== undefined && !requestHeaders["Content-Type"]) {
    requestHeaders["Content-Type"] = "application/json";
  }

  const res = await fetch(url, {
    ...init,
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
    // Never cache authenticated requests — each user's response is private.
    cache: token ? "no-store" : init.cache,
    next: token ? undefined : { revalidate: revalidateSeconds },
  });

  if (res.status === 204) {
    return undefined as T;
  }

  if (!res.ok) {
    let code = "HTTP_ERROR";
    let message = `${res.status} ${res.statusText}`;
    try {
      const payload = (await res.json()) as ApiError;
      if (payload?.error?.code) code = payload.error.code;
      if (payload?.error?.message) message = payload.error.message;
    } catch {
      /* fall through with default message */
    }
    if (res.status === 404) throw new ApiNotFoundError(code, message);
    throw new ApiRequestError(res.status, code, message);
  }

  return (await res.json()) as T;
}
