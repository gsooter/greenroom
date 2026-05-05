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

  // MapKit JS loads its bootstrap script from cdn.apple-mapkit.com, then
  // opens XHR/fetch connections to Apple's tile/config endpoints to render
  // the map. Without these on connect-src the map loads a grey background
  // and then the tile fetches fail silently.
  const mapKitScript = "https://cdn.apple-mapkit.com";
  const mapKitConnect = [
    "https://cdn.apple-mapkit.com",
    "https://cdn2.apple-mapkit.com",
    "https://cdn3.apple-mapkit.com",
    "https://cdn4.apple-mapkit.com",
    "https://*.ls.apple.com",
  ].join(" ");

  // Sentry's browser SDK posts envelope payloads to the org-specific
  // ingest subdomain (e.g. o12345.ingest.us.sentry.io). The wildcard
  // covers every Sentry region without needing the DSN at config-build
  // time.
  const sentryIngest = "https://*.ingest.sentry.io https://*.ingest.us.sentry.io";

  const connectSources = [
    "'self'",
    apiOrigin,
    appleMusicApi,
    mapKitConnect,
    sentryIngest,
    isDev && "ws://127.0.0.1:3000",
    isDev && "ws://localhost:3000",
  ]
    .filter(Boolean)
    .join(" ");

  const scriptSrc = isDev
    ? `script-src 'self' 'unsafe-inline' 'unsafe-eval' ${appleMusicScript} ${mapKitScript}`
    : `script-src 'self' 'unsafe-inline' ${appleMusicScript} ${mapKitScript}`;

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
    // Geolocation is allowed for this origin so the /near-me surface can
    // call navigator.geolocation.getCurrentPosition. An empty value would
    // disable the API for first-party code as well as embeds.
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(self), payment=()",
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

// Wrap with Sentry only when a DSN is configured. Avoids dragging the
// Sentry build plugin into local dev where contributors don't need it
// (and where it has historically caused React-duplication issues).
const sentryConfigured = Boolean(process.env.NEXT_PUBLIC_SENTRY_DSN);
let exportedConfig = nextConfig;
if (sentryConfigured) {
  const { withSentryConfig } = await import("@sentry/nextjs");
  exportedConfig = withSentryConfig(nextConfig, {
    org: process.env.SENTRY_ORG,
    project: process.env.SENTRY_PROJECT,
    silent: !process.env.CI,
    widenClientFileUpload: true,
    disableLogger: true,
  });
}

export default exportedConfig;
