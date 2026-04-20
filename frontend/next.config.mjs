/**
 * Next.js configuration — security headers are applied to every route.
 *
 * CSP sources are computed from env vars so ``connect-src`` matches
 * whatever backend the deploy is pointed at. ``'unsafe-inline'`` is
 * retained on ``script-src`` and ``style-src`` because Next.js 14 still
 * emits inline bootstrap scripts and Tailwind ships inline styles; the
 * nonce path requires middleware we haven't wired up yet. In development
 * we also allow ``'unsafe-eval'`` and ``ws:`` so React Fast Refresh and
 * webpack HMR can evaluate modules and connect back to the dev server —
 * without this, client components never hydrate and auth-aware UI stays
 * blank. Images come from a wide mix of venue/ticketing CDNs so
 * ``img-src`` allows any ``https:`` source.
 */

const isDev = process.env.NODE_ENV !== "production";

/** @type {(parts: string[]) => string} */
const csp = (parts) => parts.filter(Boolean).join("; ");

/**
 * Build the Content-Security-Policy header from env-provided allowlists.
 * @param {{ apiUrl?: string }} env
 * @returns {string}
 */
function buildContentSecurityPolicy(env) {
  const apiOrigin = safeOrigin(env.apiUrl);
  // MusicKit JS ships its script from Apple's CDN, talks to the Music
  // REST API, and renders its consent overlay inside Apple-hosted
  // iframes. All three origins need to be on the allowlist or the
  // script tag 404s and the authorize flow never renders.
  const appleMusicScript = "https://js-cdn.music.apple.com";
  const appleMusicApi = "https://api.music.apple.com";
  const appleMusicFrames = "https://*.music.apple.com";

  const connectSources = [
    "'self'",
    apiOrigin,
    appleMusicApi,
    isDev && "ws://127.0.0.1:3000",
    isDev && "ws://localhost:3000",
  ]
    .filter(Boolean)
    .join(" ");

  const scriptSrc = isDev
    ? `script-src 'self' 'unsafe-inline' 'unsafe-eval' ${appleMusicScript}`
    : `script-src 'self' 'unsafe-inline' ${appleMusicScript}`;

  return csp([
    "default-src 'self'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' https: data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSources}`,
    `frame-src ${appleMusicFrames}`,
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
