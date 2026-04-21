import { afterEach, describe, expect, it, vi } from "vitest";

import { buildDirectionsUrl, detectMapProvider } from "./maps";

describe("buildDirectionsUrl", () => {
  const base = {
    latitude: 38.917,
    longitude: -77.032,
    venueName: "Black Cat",
    address: "1811 14th St NW, Washington, DC",
  };

  it("signs Apple Maps with daddr + q label", () => {
    const url = buildDirectionsUrl("apple", base);
    expect(url).toBe(
      "https://maps.apple.com/?daddr=38.917000,-77.032000&q=Black%20Cat%2C%201811%2014th%20St%20NW%2C%20Washington%2C%20DC",
    );
  });

  it("emits a Google Maps directions URL with destination and place label", () => {
    const url = buildDirectionsUrl("google", base);
    expect(url).toContain("https://www.google.com/maps/dir/?api=1");
    expect(url).toContain("destination=38.917000,-77.032000");
    expect(url).toContain("destination_place_id=Black%20Cat%2C%20");
  });

  it("omits the address from the label when not provided", () => {
    const url = buildDirectionsUrl("apple", {
      latitude: 1,
      longitude: 2,
      venueName: "9:30 Club",
    });
    expect(url).toContain("q=9%3A30%20Club");
    expect(url).not.toContain("%2C"); // no comma means no address suffix
  });
});

describe("detectMapProvider", () => {
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

  it("returns apple for iPhone user agents", () => {
    stubUserAgent(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15",
    );
    expect(detectMapProvider()).toBe("apple");
  });

  it("returns apple for macOS Safari", () => {
    stubUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15",
    );
    expect(detectMapProvider()).toBe("apple");
  });

  it("returns google for Android Chrome", () => {
    stubUserAgent(
      "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0",
    );
    expect(detectMapProvider()).toBe("google");
  });

  it("defaults to google when navigator is unavailable (SSR)", () => {
    vi.stubGlobal("navigator", undefined);
    expect(detectMapProvider()).toBe("google");
  });
});
