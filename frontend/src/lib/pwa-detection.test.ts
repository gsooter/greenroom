/**
 * Unit tests for the PWA install/detection helpers.
 *
 * jsdom does not implement matchMedia or expose a configurable
 * `navigator.standalone`, so each test sets up the relevant globals
 * by hand. The detection helpers are tiny and self-contained — these
 * tests exercise the surface that the install / permission prompts
 * rely on.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  isAndroidChrome,
  isAppInstalled,
  isMobileBrowserInstallable,
  isMobileSafari,
} from "./pwa-detection";

const originalUserAgent = navigator.userAgent;
const originalMatchMedia = window.matchMedia;

function setUserAgent(value: string): void {
  Object.defineProperty(navigator, "userAgent", {
    configurable: true,
    get: () => value,
  });
}

function setStandalone(value: boolean | undefined): void {
  Object.defineProperty(navigator, "standalone", {
    configurable: true,
    get: () => value,
  });
}

function setMatchMedia(matches: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches,
      media: "",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

afterEach(() => {
  setUserAgent(originalUserAgent);
  setStandalone(undefined);
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  });
});

describe("isAppInstalled", () => {
  it("is true when display-mode standalone matches", () => {
    setMatchMedia(true);
    expect(isAppInstalled()).toBe(true);
  });

  it("is true when navigator.standalone is true (iOS)", () => {
    setMatchMedia(false);
    setStandalone(true);
    expect(isAppInstalled()).toBe(true);
  });

  it("is false in a regular browser tab", () => {
    setMatchMedia(false);
    setStandalone(false);
    expect(isAppInstalled()).toBe(false);
  });
});

describe("isMobileSafari", () => {
  it("matches iPhone Mobile Safari", () => {
    setUserAgent(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1",
    );
    expect(isMobileSafari()).toBe(true);
  });

  it("rejects Chrome iOS", () => {
    setUserAgent(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120.0.0.0 Mobile/15E148",
    );
    expect(isMobileSafari()).toBe(false);
  });

  it("rejects Android Chrome", () => {
    setUserAgent(
      "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
    );
    expect(isMobileSafari()).toBe(false);
  });
});

describe("isAndroidChrome", () => {
  it("matches Android Chrome", () => {
    setUserAgent(
      "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
    );
    expect(isAndroidChrome()).toBe(true);
  });

  it("matches Samsung Internet", () => {
    setUserAgent(
      "Mozilla/5.0 (Linux; Android 14; SM-S908U) AppleWebKit/537.36 SamsungBrowser/22.0 Chrome/115.0.0.0 Mobile Safari/537.36",
    );
    expect(isAndroidChrome()).toBe(true);
  });

  it("rejects iOS Safari", () => {
    setUserAgent(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1",
    );
    expect(isAndroidChrome()).toBe(false);
  });
});

describe("isMobileBrowserInstallable", () => {
  it("is true on iOS Safari", () => {
    setUserAgent(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1",
    );
    expect(isMobileBrowserInstallable()).toBe(true);
  });

  it("is false on desktop browsers", () => {
    setUserAgent(
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    );
    expect(isMobileBrowserInstallable()).toBe(false);
  });
});
