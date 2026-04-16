/** Environment variable configuration — all env vars accessed here, nowhere else. */

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const config = {
  apiUrl: requireEnv("NEXT_PUBLIC_API_URL"),
  baseUrl: requireEnv("NEXT_PUBLIC_BASE_URL"),
  posthogKey: process.env["NEXT_PUBLIC_POSTHOG_KEY"] ?? "",
} as const;
