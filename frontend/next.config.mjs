/**
 * Next.js configuration — security headers are applied to every route.
 *
 * CSP sources are computed from env vars so ``connect-src`` matches
 * whatever backend the deploy is pointed at. ``'unsafe-inline'`` is
 * retained on ``script-src`` and ``style-src`` because Next.js 14 still
 * emits inline bootstrap scripts and Tailwind ships inline styles; the
 * nonce path requires middleware we haven't wired up yet. Images come
 * from a wide mix of venue/ticketing CDNs so ``img-src`` allows any
 * ``https:`` source.
 */

/** @type {(parts: string[]) => string} */
const csp = (parts) => parts.filter(Boolean).join("; ");

/**
 * Build the Content-Security-Policy header from env-provided allowlists.
 * @param {{ apiUrl?: string }} env
 * @returns {string}
 */
function buildContentSecurityPolicy(env) {
  const apiOrigin = safeOrigin(env.apiUrl);
  const connectSources = ["'self'", apiOrigin].filter(Boolean).join(" ");

  return csp([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' https: data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSources}`,
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ]);
}

/**
 * Return the origin (scheme + host + port) of a URL string, or "" if
 * parsing fails or the input is absent.
 * @param {string | undefined} value
 * @returns {string}
 */
function safeOrigin(value) {
  if (!value) return "";
  try {
    return new URL(value).origin;
  } catch {
    return "";
  }
}

const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: buildContentSecurityPolicy({ apiUrl: process.env.NEXT_PUBLIC_API_URL }),
  },
  {
    key: "Strict-Transport-Security",
    value: "max-age=31536000; includeSubDomains",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=()",
  },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
