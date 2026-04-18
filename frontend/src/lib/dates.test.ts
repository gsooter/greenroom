/**
 * Tests for date utilities.
 *
 * Dates are anchored to America/New_York so every assertion that
 * mentions "today" / "tomorrow" is thought of in ET, not in UTC or
 * the runner's local zone.
 */

import { describe, expect, it } from "vitest";

import {
  addDaysToKey,
  bucketizeEvents,
  etDateKey,
  etDayOfWeek,
  etMonthOf,
  monthDateRange,
  monthKey,
  parseMonthKey,
  shiftMonth,
  windowDateRange,
} from "@/lib/dates";
import type { EventSummary } from "@/types";

function eventAt(starts_at: string | null, id = crypto.randomUUID()): EventSummary {
  return {
    id,
    title: `Show ${id.slice(0, 4)}`,
    slug: `show-${id.slice(0, 4)}`,
    starts_at,
    artists: [],
    image_url: null,
    min_price: null,
    max_price: null,
    status: "confirmed",
    venue: null,
  };
}

// ---------------------------------------------------------------------------
// Primitive helpers
// ---------------------------------------------------------------------------

describe("etDateKey", () => {
  it("returns YYYY-MM-DD in ET", () => {
    // 12:00 UTC on Apr 17 is always Apr 17 in ET (ET is UTC-4/5).
    const d = new Date(Date.UTC(2026, 3, 17, 12, 0, 0));
    expect(etDateKey(d)).toBe("2026-04-17");
  });

  it("handles ET/UTC rollover across the evening", () => {
    // 03:00 UTC on Apr 18 = 23:00 ET on Apr 17.
    const d = new Date(Date.UTC(2026, 3, 18, 3, 0, 0));
    expect(etDateKey(d)).toBe("2026-04-17");
  });
});

describe("etDayOfWeek", () => {
  it("maps Monday through Sunday", () => {
    // Mon 2026-04-13 at noon UTC
    const mon = new Date(Date.UTC(2026, 3, 13, 12));
    const sun = new Date(Date.UTC(2026, 3, 19, 12));
    expect(etDayOfWeek(mon)).toBe(1);
    expect(etDayOfWeek(sun)).toBe(0);
  });
});

describe("addDaysToKey", () => {
  it("advances by positive days across month boundary", () => {
    expect(addDaysToKey("2026-04-29", 3)).toBe("2026-05-02");
  });

  it("goes backward with a negative offset", () => {
    expect(addDaysToKey("2026-04-03", -5)).toBe("2026-03-29");
  });

  it("handles leap-year February", () => {
    expect(addDaysToKey("2024-02-28", 1)).toBe("2024-02-29");
    expect(addDaysToKey("2024-02-29", 1)).toBe("2024-03-01");
  });
});

describe("etMonthOf + monthKey + parseMonthKey", () => {
  it("round-trips year/month", () => {
    const { year, monthIndex } = etMonthOf(
      new Date(Date.UTC(2026, 3, 17, 12)),
    );
    expect(year).toBe(2026);
    expect(monthIndex).toBe(3);
    expect(monthKey(year, monthIndex)).toBe("2026-04");
    expect(parseMonthKey("2026-04")).toEqual({ year: 2026, monthIndex: 3 });
  });

  it("returns null on invalid month keys", () => {
    expect(parseMonthKey(undefined)).toBeNull();
    expect(parseMonthKey("2026")).toBeNull();
    expect(parseMonthKey("2026-13")).toBeNull();
    expect(parseMonthKey("2026-00")).toBeNull();
  });
});

describe("monthDateRange", () => {
  it("covers the full month", () => {
    expect(monthDateRange(2026, 3)).toEqual({
      dateFrom: "2026-04-01",
      dateTo: "2026-04-30",
    });
  });

  it("handles 31-day months and February", () => {
    expect(monthDateRange(2026, 0).dateTo).toBe("2026-01-31");
    expect(monthDateRange(2026, 1).dateTo).toBe("2026-02-28");
    expect(monthDateRange(2024, 1).dateTo).toBe("2024-02-29");
  });
});

describe("shiftMonth", () => {
  it("moves forward without wrap", () => {
    expect(shiftMonth(2026, 3, 2)).toEqual({ year: 2026, monthIndex: 5 });
  });

  it("wraps across year boundaries forward", () => {
    expect(shiftMonth(2026, 11, 1)).toEqual({ year: 2027, monthIndex: 0 });
  });

  it("wraps across year boundaries backward", () => {
    expect(shiftMonth(2026, 0, -1)).toEqual({ year: 2025, monthIndex: 11 });
  });
});

// ---------------------------------------------------------------------------
// windowDateRange — named windows anchored to ET "today"
// ---------------------------------------------------------------------------

describe("windowDateRange", () => {
  it("tonight is a single-day range", () => {
    // Tue 2026-04-14 noon UTC is Tue in ET.
    const now = new Date(Date.UTC(2026, 3, 14, 16));
    const range = windowDateRange("tonight", now);
    expect(range.dateFrom).toBe(range.dateTo);
  });

  it("week spans today + 6 days", () => {
    const now = new Date(Date.UTC(2026, 3, 14, 16));
    const { dateFrom, dateTo } = windowDateRange("week", now);
    expect(dateFrom).toBe("2026-04-14");
    expect(dateTo).toBe("2026-04-20");
  });

  it("weekend from a weekday starts at the upcoming Friday", () => {
    // Tue 2026-04-14: Fri 04-17, Sun 04-19.
    const tue = new Date(Date.UTC(2026, 3, 14, 16));
    expect(windowDateRange("weekend", tue)).toEqual({
      dateFrom: "2026-04-17",
      dateTo: "2026-04-19",
    });
  });

  it("weekend on Friday includes today", () => {
    // Fri 2026-04-17.
    const fri = new Date(Date.UTC(2026, 3, 17, 16));
    expect(windowDateRange("weekend", fri)).toEqual({
      dateFrom: "2026-04-17",
      dateTo: "2026-04-19",
    });
  });

  it("weekend on Sunday is today only", () => {
    const sun = new Date(Date.UTC(2026, 3, 19, 16));
    expect(windowDateRange("weekend", sun)).toEqual({
      dateFrom: "2026-04-19",
      dateTo: "2026-04-19",
    });
  });
});

// ---------------------------------------------------------------------------
// bucketizeEvents — the sticky-header grouping on /events
// ---------------------------------------------------------------------------

describe("bucketizeEvents", () => {
  // Anchor every bucketize test to Tue 2026-04-14 16:00 UTC (12:00 ET).
  const NOW = new Date(Date.UTC(2026, 3, 14, 16));

  function isoFor(key: string, hourUtc = 20): string {
    // 20:00 UTC ≈ 16:00 ET, firmly inside the ET day.
    return `${key}T${String(hourUtc).padStart(2, "0")}:00:00.000Z`;
  }

  it("drops events without a valid starts_at", () => {
    const rows = bucketizeEvents(
      [eventAt(null), eventAt("not-a-date")],
      NOW,
    );
    expect(rows).toEqual([]);
  });

  it("groups today as Tonight", () => {
    const rows = bucketizeEvents([eventAt(isoFor("2026-04-14"))], NOW);
    expect(rows.map((b) => b.key)).toEqual(["tonight"]);
    expect(rows[0]!.label).toBe("Tonight");
  });

  it("groups weekend separately from next-7 and excludes today", () => {
    const rows = bucketizeEvents(
      [
        eventAt(isoFor("2026-04-14"), "a"), // Tonight
        eventAt(isoFor("2026-04-15"), "b"), // Next 7 days (Wed)
        eventAt(isoFor("2026-04-17"), "c"), // Weekend (Fri)
        eventAt(isoFor("2026-04-19"), "d"), // Weekend (Sun)
      ],
      NOW,
    );
    const keys = rows.map((b) => b.key);
    expect(keys).toContain("tonight");
    expect(keys).toContain("weekend");
    expect(keys).toContain("next7");

    const weekend = rows.find((b) => b.key === "weekend");
    expect(weekend?.events.map((e) => e.id).sort()).toEqual(["c", "d"]);
    const next7 = rows.find((b) => b.key === "next7");
    expect(next7?.events.map((e) => e.id)).toEqual(["b"]);
  });

  it("buckets beyond 7 days by calendar week starting Monday", () => {
    const rows = bucketizeEvents(
      [
        eventAt(isoFor("2026-04-27"), "apr27"), // Mon, Week of Apr 27
        eventAt(isoFor("2026-04-30"), "apr30"), // Thu, same week
        eventAt(isoFor("2026-05-04"), "may04"), // Mon, Week of May 4
      ],
      NOW,
    );
    const keys = rows.map((b) => b.key);
    expect(keys).toEqual(["week:2026-04-27", "week:2026-05-04"]);
    const first = rows[0]!;
    expect(first.label).toMatch(/^Week of /);
    expect(first.events.map((e) => e.id).sort()).toEqual(["apr27", "apr30"]);
  });

  it("preserves registration order of buckets", () => {
    // Feed a week bucket first so registration order isn't chronological.
    const rows = bucketizeEvents(
      [
        eventAt(isoFor("2026-04-27"), "later"),
        eventAt(isoFor("2026-04-14"), "tonight"),
      ],
      NOW,
    );
    expect(rows.map((b) => b.key)).toEqual([
      "week:2026-04-27",
      "tonight",
    ]);
  });
});
