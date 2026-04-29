import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import GetDirectionsButton from "./GetDirectionsButton";

const BASE_PROPS = {
  venueName: "Black Cat",
  latitude: 38.917,
  longitude: -77.032,
  address: "1811 14th St NW, Washington, DC",
} as const;

describe("GetDirectionsButton", () => {
  const originalNav = globalThis.navigator;

  afterEach(() => {
    Object.defineProperty(globalThis, "navigator", {
      value: originalNav,
      configurable: true,
    });
  });

  function stubUserAgent(ua: string): void {
    Object.defineProperty(globalThis, "navigator", {
      value: { userAgent: ua },
      configurable: true,
    });
  }

  it("renders a Google Maps href on Android", async () => {
    stubUserAgent(
      "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36",
    );
    render(<GetDirectionsButton {...BASE_PROPS} />);
    const link = await screen.findByRole("link");
    expect(link.getAttribute("href")).toContain(
      "https://www.google.com/maps/dir/?api=1",
    );
    expect(link.getAttribute("href")).toContain(
      "destination=38.917000,-77.032000",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("upgrades to Apple Maps on iOS after hydration", async () => {
    stubUserAgent(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15",
    );
    render(<GetDirectionsButton {...BASE_PROPS} />);
    const link = await screen.findByRole("link");
    // Wait for the useEffect to run (React auto-flushes sync effects in tests).
    expect(link.getAttribute("href")).toContain("https://maps.apple.com/");
    expect(link.getAttribute("aria-label")).toContain("Apple Maps");
  });

  it("supports a className override", async () => {
    stubUserAgent("Mozilla/5.0 (X11; Linux x86_64)");
    render(<GetDirectionsButton {...BASE_PROPS} className="my-custom-class" />);
    const link = await screen.findByRole("link");
    expect(link.className).toBe("my-custom-class");
  });
});
