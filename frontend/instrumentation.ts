/**
 * Next.js instrumentation entry point.
 *
 * Picked up automatically by Next.js at boot. Loads the Sentry SDK
 * appropriate to whichever runtime the request is executing in
 * (Node server vs. edge). The browser SDK is loaded separately via
 * sentry.client.config.ts at the App Router level.
 */

export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

export { captureRequestError as onRequestError } from "@sentry/nextjs";
