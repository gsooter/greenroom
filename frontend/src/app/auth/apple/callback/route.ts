/**
 * /auth/apple/callback — form-post landing for Sign-in-with-Apple.
 *
 * Apple uses `response_mode=form_post`, so the browser arrives here as
 * an `application/x-www-form-urlencoded` POST carrying `code`, `state`,
 * and (only on first sign-in) a JSON `user` payload.
 *
 * Next.js client pages cannot receive a POST directly, so this route
 * handler:
 *   1. Parses the form values.
 *   2. Re-encodes them on a 303 redirect to a sibling client page
 *      (`/auth/apple/callback/complete`), which calls the backend
 *      and persists the JWT via AuthContext.
 */

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/**
 * Handle Apple's form_post redirect and forward to the client page.
 */
export async function POST(request: Request): Promise<NextResponse> {
  const form = await request.formData();
  const code = stringOrEmpty(form.get("code"));
  const state = stringOrEmpty(form.get("state"));
  const providerError = stringOrEmpty(form.get("error"));
  const userRaw = stringOrEmpty(form.get("user"));

  const target = new URL("/auth/apple/callback/complete", request.url);
  if (providerError) target.searchParams.set("error", providerError);
  if (code) target.searchParams.set("code", code);
  if (state) target.searchParams.set("state", state);
  if (userRaw) target.searchParams.set("user", userRaw);

  return NextResponse.redirect(target, 303);
}

/**
 * GET here is only hit if someone visits the URL directly (never from
 * Apple). Bounce them to /login — there is nothing to complete.
 */
export async function GET(request: Request): Promise<NextResponse> {
  return NextResponse.redirect(new URL("/login", request.url), 303);
}

function stringOrEmpty(value: FormDataEntryValue | null): string {
  if (value === null) return "";
  if (typeof value === "string") return value;
  return "";
}
