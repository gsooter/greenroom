import type { MetadataRoute } from "next";

import { brandColors } from "@/lib/brand";

/**
 * Web App Manifest, served by Next.js at /manifest.webmanifest.
 *
 * The presence of this file plus the Apple-specific meta tags wired
 * into app/layout.tsx is what makes Greenroom installable to a phone
 * home screen. iOS Safari requires the home-screen install before
 * push notifications are allowed at all (Apple gates Web Push behind
 * "added to Home Screen") — so this manifest is the foundation for
 * push, not just a polish nicety.
 *
 * Color values come from src/lib/brand.ts so the manifest stays in
 * lockstep with globals.css. The maskable icon has 20% padding around
 * the visible mark so Android adaptive icons can crop it to a circle
 * or rounded square without slicing the logo.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Greenroom",
    short_name: "Greenroom",
    description:
      "Every DMV concert in one calendar — personalized for you.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: brandColors.bgBase,
    theme_color: brandColors.greenDark,
    icons: [
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    categories: ["music", "entertainment", "lifestyle"],
    lang: "en-US",
    dir: "ltr",
  };
}
