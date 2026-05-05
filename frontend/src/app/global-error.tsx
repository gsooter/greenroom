"use client";

/**
 * Root-layout error boundary.
 *
 * Next.js renders this only when the error happens inside the root
 * layout itself (which means the regular app/error.tsx couldn't catch
 * it). Because the root layout is gone, this file owns the html and
 * body tags and ships an inline minimal style so the screen isn't
 * unstyled. Sentry still receives the exception when configured.
 */

import { useEffect } from "react";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    // See app/error.tsx for why this guard exists — webpack drops the
    // dynamic import entirely when NEXT_PUBLIC_SENTRY_DSN is empty.
    if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
      void import("@sentry/nextjs").then((Sentry) => {
        Sentry.captureException(error);
      });
    }
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#f7f0ee",
          color: "#1a2820",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          padding: "1.5rem",
        }}
      >
        <main
          style={{
            maxWidth: "32rem",
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            gap: "1rem",
          }}
        >
          <h1 style={{ fontSize: "1.5rem", margin: 0 }}>
            Something broke loading Greenroom
          </h1>
          <p style={{ color: "#7a6a65", margin: 0, fontSize: "0.875rem" }}>
            A fatal error stopped the page from rendering. The team has been
            notified.
          </p>
          {error.digest ? (
            <p style={{ color: "#7a6a65", margin: 0, fontSize: "0.75rem" }}>
              Reference: {error.digest}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              alignSelf: "center",
              borderRadius: "9999px",
              backgroundColor: "#2d5a3d",
              color: "#f7f0ee",
              padding: "0.5rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 500,
              border: "none",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
