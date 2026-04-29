/**
 * Sentry browser SDK initialization.
 *
 * Auto-loaded by @sentry/nextjs on the client side. The SDK is a no-op
 * when NEXT_PUBLIC_SENTRY_DSN is empty so dev contributors don't need
 * a Sentry account to run the app.
 */

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    tracesSampleRate: 0,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
  });
}
