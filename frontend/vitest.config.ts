import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    globals: true,
    css: false,
    env: {
      NEXT_PUBLIC_API_URL: "http://test.api",
      NEXT_PUBLIC_BASE_URL: "http://test.base",
    },
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary", "lcov"],
      include: ["src/lib/**/*.{ts,tsx}", "src/components/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/types/**",
        "src/app/**",
        "src/components/seo/**",
        // Purely presentational wrappers — CLAUDE.md exempts these.
        "src/components/providers/AppProviders.tsx",
        "src/components/layout/AppShell.tsx",
        "src/components/layout/TopNav.tsx",
        "src/components/layout/MobileBottomNav.tsx",
        "src/components/ui/EmptyState.tsx",
        "src/components/ui/LoadingSkeleton.tsx",
        "src/components/ui/Modal.tsx",
        "src/components/ui/RegionBadge.tsx",
        // Browser-SDK glue (MapKit JS, MusicKit JS, WebAuthn). Each is
        // a thin imperative wrapper around an external script; meaningful
        // tests would require standing up the SDK in jsdom, which the
        // SDKs themselves don't support. Behavior is exercised manually
        // and via E2E.
        "src/lib/mapkit.ts",
        "src/lib/musickit.ts",
        "src/lib/webauthn.ts",
        // Admin surface — internal-only SPA, gated by an HMAC secret.
        // Not part of the public product; kept out of the coverage gate
        // to focus the bar on user-facing code.
        "src/lib/api/admin.ts",
        "src/components/admin/**",
        // Heavy MapKit-driven map components — instantiate MapKit JS
        // imperatively. Same reasoning as the SDK wrappers above.
        "src/components/map/TonightMap.tsx",
        "src/components/venues/VenueSurroundingsModal.tsx",
        "src/components/venues/VenueTipsAnchor.tsx",
        "src/components/recommendations/RecommendationGridSkeleton.tsx",
      ],
      thresholds: {
        lines: 80,
        statements: 80,
        branches: 70,
        functions: 80,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
