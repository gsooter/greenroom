/**
 * Brand color hexes mirrored from styles/globals.css.
 *
 * Build-time consumers (Web App Manifest, Apple meta tags, OpenGraph
 * background fallbacks) cannot read CSS custom properties — those
 * exist only at runtime in the browser. This file is the single
 * source of truth for the same hex values, kept in lockstep with
 * the tokens defined in :root in globals.css. Changing one without
 * the other is a bug.
 */

export const brandColors = {
  /** Petal Mist — page background; PWA splash background. */
  bgBase: "#F7F0EE",
  /** Deep Forest — top nav and PWA theme color. */
  greenDark: "#1E3D2A",
  /** Forest Primary — primary CTAs, push notification badge. */
  greenPrimary: "#2D5A3D",
  /** Petal Pink — recommendation surfaces. */
  blushSoft: "#F5D5D0",
  /** Deep Canopy — primary text. */
  textPrimary: "#1A2820",
} as const;

export type BrandColorKey = keyof typeof brandColors;
