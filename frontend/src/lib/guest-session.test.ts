/**
 * Tests for the guest-session id helper.
 *
 * The id is persisted in localStorage when available, with a process-
 * lifetime in-memory fallback for environments where storage is
 * disabled (Safari private mode, etc.).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STORAGE_KEY = "greenroom.guest_session";

async function freshModule() {
  vi.resetModules();
  return await import("@/lib/guest-session");
}

describe("getGuestSessionId", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a new id on first call and persists it to localStorage", async () => {
    const { getGuestSessionId } = await freshModule();
    const id = getGuestSessionId();
    expect(id).toBeTruthy();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(id);
  });

  it("returns the same id on subsequent calls", async () => {
    const { getGuestSessionId } = await freshModule();
    const a = getGuestSessionId();
    const b = getGuestSessionId();
    expect(a).toBe(b);
  });

  it("reuses an existing id already in localStorage", async () => {
    window.localStorage.setItem(STORAGE_KEY, "existing-id");
    const { getGuestSessionId } = await freshModule();
    expect(getGuestSessionId()).toBe("existing-id");
  });

  it("falls back to an in-memory id when localStorage throws", async () => {
    const { getGuestSessionId } = await freshModule();
    const spy = vi
      .spyOn(window.localStorage.__proto__, "getItem")
      .mockImplementation(() => {
        throw new Error("denied");
      });
    const a = getGuestSessionId();
    const b = getGuestSessionId();
    expect(a).toBeTruthy();
    expect(a).toBe(b);
    spy.mockRestore();
  });

  it("uses the timestamp+random fallback when crypto.randomUUID is missing", async () => {
    const original = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", {
      configurable: true,
      value: {},
    });
    try {
      const { getGuestSessionId } = await freshModule();
      const id = getGuestSessionId();
      expect(id.startsWith("g-")).toBe(true);
    } finally {
      Object.defineProperty(globalThis, "crypto", {
        configurable: true,
        value: original,
      });
    }
  });
});
