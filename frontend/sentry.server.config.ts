/**
 * Sentry SDK initialization for the Next.js server runtime.
 *
 * Loaded by instrumentation.ts on the Node side. No-op when
 * NEXT_PUBLIC_SENTRY_DSN is empty.
 */

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 0,
  });
}
