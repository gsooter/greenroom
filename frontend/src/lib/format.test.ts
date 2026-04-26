/**
 * Tests for pure formatting helpers.
 */

import { describe, expect, it } from "vitest";

import {
  formatEventDate,
  formatEventTime,
  formatLongDate,
  formatPriceRange,
  formatRelativeTime,
  joinArtists,
} from "@/lib/format";

describe("formatEventDate", () => {
  it("formats a valid ISO datetime in ET", () => {
    // 20:00 UTC on Apr 17 2026 — 16:00 ET, Friday.
    expect(formatEventDate("2026-04-17T20:00:00.000Z")).toMatch(/Fri/);
  });

  it("returns 'Date TBA' for null and invalid strings", () => {
    expect(formatEventDate(null)).toBe("Date TBA");
    expect(formatEventDate("not-a-date")).toBe("Date TBA");
  });
});

describe("formatEventTime", () => {
  it("returns a time string for a valid ISO", () => {
    const out = formatEventTime("2026-04-17T20:00:00.000Z");
    expect(out).toBeTruthy();
    expect(out).toMatch(/\d/);
  });

  it("returns null for invalid or missing input", () => {
    expect(formatEventTime(null)).toBeNull();
    expect(formatEventTime("x")).toBeNull();
  });
});

describe("formatLongDate", () => {
  it("returns a weekday-name-level long date for a valid ISO", () => {
    const out = formatLongDate("2026-04-17T20:00:00.000Z");
    expect(out).toMatch(/2026/);
    expect(out).toMatch(/April/);
  });

  it("returns fallback for null and bad input", () => {
    expect(formatLongDate(null)).toBe("Date TBA");
    expect(formatLongDate("bad")).toBe("Date TBA");
  });
});

describe("formatPriceRange", () => {
  it("returns null when both min and max are null", () => {
    expect(formatPriceRange(null, null)).toBeNull();
  });

  it("returns a range when min and max differ", () => {
    expect(formatPriceRange(10.4, 40.9)).toBe("$10–$41");
  });

  it("returns 'From $X' when only one side is populated", () => {
    expect(formatPriceRange(null, 25)).toBe("From $25");
    expect(formatPriceRange(25, null)).toBe("From $25");
  });

  it("collapses equal min and max into a single 'From $X'", () => {
    expect(formatPriceRange(30, 30)).toBe("From $30");
  });
});

describe("formatRelativeTime", () => {
  const NOW = new Date("2026-04-26T12:00:00Z");

  it("returns 'never' for null and invalid input", () => {
    expect(formatRelativeTime(null, NOW)).toBe("never");
    expect(formatRelativeTime("not-a-date", NOW)).toBe("never");
  });

  it("returns 'just now' for under one minute", () => {
    const t = new Date(NOW.getTime() - 30_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("just now");
  });

  it("singular 'minute' at exactly 1m", () => {
    const t = new Date(NOW.getTime() - 60_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("1 minute ago");
  });

  it("plural minutes under an hour", () => {
    const t = new Date(NOW.getTime() - 5 * 60_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("5 minutes ago");
  });

  it("hours roll up at 60m", () => {
    const t = new Date(NOW.getTime() - 60 * 60_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("1 hour ago");
  });

  it("days roll up at 24h", () => {
    const t = new Date(NOW.getTime() - 25 * 60 * 60_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toBe("1 day ago");
  });

  it("falls back to long date past one week", () => {
    const t = new Date(NOW.getTime() - 9 * 24 * 60 * 60_000).toISOString();
    expect(formatRelativeTime(t, NOW)).toMatch(/2026/);
  });
});

describe("joinArtists", () => {
  it("returns null for empty input", () => {
    expect(joinArtists(null)).toBeNull();
    expect(joinArtists(undefined)).toBeNull();
    expect(joinArtists([])).toBeNull();
  });

  it("joins with comma when under limit", () => {
    expect(joinArtists(["A", "B"])).toBe("A, B");
  });

  it("adds '+N more' when exceeding limit", () => {
    expect(joinArtists(["A", "B", "C", "D", "E"], 3)).toBe("A, B, C +2 more");
  });

  it("does not add '+more' when exactly at limit", () => {
    expect(joinArtists(["A", "B", "C"], 3)).toBe("A, B, C");
  });
});
