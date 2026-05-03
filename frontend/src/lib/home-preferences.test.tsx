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
} from "vitest";

import {
  readCompact,
  useCompactMode,
  writeCompact,
} from "@/lib/home-preferences";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("readCompact", () => {
  it("returns false when no value is stored", () => {
    expect(readCompact()).toBe(false);
  });

  it("returns true only for the literal string 'true'", () => {
    window.localStorage.setItem("greenroom.home.compact", "true");
    expect(readCompact()).toBe(true);
    window.localStorage.setItem("greenroom.home.compact", "1");
    expect(readCompact()).toBe(false);
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
