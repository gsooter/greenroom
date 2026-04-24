/**
 * Homepage Open Graph image — generated at request time via Next.js
 * `ImageResponse`. Returns a branded 1200×630 PNG that iMessage, Slack,
 * Twitter, and other platforms render when the site root is shared.
 *
 * Scoped to `/` via the App Router file convention. Event and venue
 * pages continue to use their own `image_url`-backed previews — this
 * file intentionally does not apply to them.
 */

import { ImageResponse } from "next/og";

export const runtime = "edge";

export const alt = "Greenroom — DC concert calendar";

export const size = {
  width: 1200,
  height: 630,
};

export const contentType = "image/png";

const BACKGROUND = "#F7F0EE";
const FOREST = "#1E3D2A";
const DUSTY_ROSE = "#7A6A65";

/**
 * Renders the homepage Open Graph image.
 *
 * Returns:
 *     An `ImageResponse` emitting a 1200×630 PNG with the GREENROOM
 *     wordmark, a thin rule, and the tagline "DC's concert calendar"
 *     centered on a Petal Mist background.
 */
export default async function Image(): Promise<ImageResponse> {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: BACKGROUND,
        }}
      >
        <div
          style={{
            fontSize: 168,
            fontWeight: 600,
            letterSpacing: "-0.04em",
            color: FOREST,
            lineHeight: 1,
          }}
        >
          GREENROOM
        </div>
        <div
          style={{
            width: 120,
            height: 2,
            backgroundColor: FOREST,
            opacity: 0.35,
            margin: "44px 0",
          }}
        />
        <div
          style={{
            fontSize: 40,
            fontWeight: 400,
            letterSpacing: "-0.01em",
            color: DUSTY_ROSE,
          }}
        >
          {"DC\u2019s concert calendar"}
        </div>
      </div>
    ),
    { ...size },
  );
}
