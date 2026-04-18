/**
 * Environment variable configuration — all env vars accessed here.
 *
 * Next.js only inlines `NEXT_PUBLIC_*` references written as literal
 * `process.env.NAME` (dot access) at build time. Bracket access like
 * `process.env["NAME"]` is not substituted, so on the browser side the
 * value is `undefined`. Keep the dot-access form below — do not refactor
 * it behind a dynamic helper.
 */

function requireEnv(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

const publicApiUrl = requireEnv(
  "NEXT_PUBLIC_API_URL",
  process.env.NEXT_PUBLIC_API_URL,
);

// In Docker, the frontend container can't reach "localhost" to talk to the
// backend — it needs the compose-network hostname (e.g. http://backend:5001).
// SERVER_API_URL lets us override the URL used during SSR while keeping
// NEXT_PUBLIC_API_URL pointed at the host-mapped port for the browser.
const serverApiUrl = process.env.SERVER_API_URL || publicApiUrl;

export const config = {
  apiUrl: typeof window === "undefined" ? serverApiUrl : publicApiUrl,
  publicApiUrl,
  baseUrl: requireEnv("NEXT_PUBLIC_BASE_URL", process.env.NEXT_PUBLIC_BASE_URL),
  posthogKey: process.env.NEXT_PUBLIC_POSTHOG_KEY ?? "",
  spotifyLoginEnabled:
    process.env.NEXT_PUBLIC_SPOTIFY_LOGIN_ENABLED === "true",
} as const;
