/**
 * Tests for the compact-mode preference hook.
 *
 * Covers: SSR-safe defaults, localStorage round-trip, the same-tab
 * change-event subscription that keeps multiple consumers in sync,
 * and graceful degradation when localStorage is unavailable.
 */

import { act, renderHook } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  readCompact,
  useCompactMode,
  writeCompact,
} from "@/lib/home-preferences";

const realMatchMedia = window.matchMedia;

function setViewport(matchesMobile: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: matchesMobile && /max-width:\s*\d+px/.test(query),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  window.localStorage.clear();
  setViewport(false);
});

afterEach(() => {
  window.localStorage.clear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: realMatchMedia,
  });
});

describe("readCompact", () => {
  it("falls back to comfortable on a desktop viewport when nothing is stored", () => {
    setViewport(false);
    expect(readCompact()).toBe(false);
  });

  it("falls back to compact on a mobile viewport when nothing is stored", () => {
    setViewport(true);
    expect(readCompact()).toBe(true);
  });

  it("honors a stored 'true' regardless of viewport", () => {
    setViewport(false);
    window.localStorage.setItem("greenroom.home.compact", "true");
    expect(readCompact()).toBe(true);
  });

  it("honors a stored 'false' on mobile (explicit opt-out wins)", () => {
    setViewport(true);
    window.localStorage.setItem("greenroom.home.compact", "false");
    expect(readCompact()).toBe(false);
  });

  it("treats unknown stored values as 'no preference' (viewport default)", () => {
    setViewport(false);
    window.localStorage.setItem("greenroom.home.compact", "1");
    expect(readCompact()).toBe(false);
    setViewport(true);
    expect(readCompact()).toBe(true);
  });
});

describe("writeCompact", () => {
  it("persists the value as the literal string", () => {
    writeCompact(true);
    expect(window.localStorage.getItem("greenroom.home.compact")).toBe("true");
    writeCompact(false);
    expect(window.localStorage.getItem("greenroom.home.compact")).toBe("false");
  });
});

describe("useCompactMode", () => {
  it("rehydrates from storage on mount", () => {
    window.localStorage.setItem("greenroom.home.compact", "true");
    const { result } = renderHook(() => useCompactMode());
    expect(result.current[0]).toBe(true);
  });

  it("flips when the setter is called", () => {
    const { result } = renderHook(() => useCompactMode());
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
    expect(window.localStorage.getItem("greenroom.home.compact")).toBe("true");
  });

  it("syncs across two consumers in the same tab", () => {
    const a = renderHook(() => useCompactMode());
    const b = renderHook(() => useCompactMode());
    act(() => a.result.current[1](true));
    expect(b.result.current[0]).toBe(true);
  });
});
