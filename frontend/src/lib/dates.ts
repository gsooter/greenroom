/**
 * Date utilities anchored to America/New_York, the canonical timezone
 * for all DMV show scheduling. Every bucketing and window computation
 * treats "today" as whatever day it currently is in ET, regardless of
 * the viewer's browser locale, so a user in California doesn't see
 * tomorrow's shows labeled "Tonight".
 */

import type { EventSummary } from "@/types";

export type DateWindow = "tonight" | "weekend" | "week";

export interface DateBucket {
  key: string;
  label: string;
  events: EventSummary[];
}

export interface DateRange {
  dateFrom: string;
  dateTo: string;
}

const ISO_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const ET_YEAR = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
});

const ET_MONTH = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "numeric",
});

const WEEKDAY = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
});

const BUCKET_HEADER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
});

const DAY_SHORT_NAMES: Record<string, number> = {
  Sun: 0,
  Mon: 1,
  Tue: 2,
  Wed: 3,
  Thu: 4,
  Fri: 5,
  Sat: 6,
};

/**
 * Returns the YYYY-MM-DD key for a Date, evaluated in America/New_York.
 */
export function etDateKey(date: Date): string {
  return ISO_DATE.format(date);
}

/**
 * Returns the ET weekday index (0=Sun..6=Sat) for a Date.
 */
export function etDayOfWeek(date: Date): number {
  return DAY_SHORT_NAMES[WEEKDAY.format(date)] ?? 0;
}

/**
 * Adds `n` days to a YYYY-MM-DD key and returns the new key.
 */
export function addDaysToKey(key: string, n: number): string {
  const [y, m, d] = key.split("-").map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d));
  date.setUTCDate(date.getUTCDate() + n);
  const yy = date.getUTCFullYear();
  const mm = String(date.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(date.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

/**
 * Returns the display label for a bucket header (e.g. "Apr 27").
 */
function headerFromKey(key: string): string {
  const [y, m, d] = key.split("-").map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d, 12));
  return BUCKET_HEADER.format(date);
}

/**
 * Returns {year, monthIndex} in ET for the given Date.
 */
export function etMonthOf(date: Date): { year: number; monthIndex: number } {
  const year = Number.parseInt(ET_YEAR.format(date), 10);
  const monthIndex = Number.parseInt(ET_MONTH.format(date), 10) - 1;
  return { year, monthIndex };
}

/**
 * Parses a `YYYY-MM` month key. Returns null if the string is invalid.
 */
export function parseMonthKey(
  value: string | undefined,
): { year: number; monthIndex: number } | null {
  if (!value || !/^\d{4}-\d{2}$/.test(value)) return null;
  const [y, m] = value.split("-").map(Number) as [number, number];
  if (m < 1 || m > 12) return null;
  return { year: y, monthIndex: m - 1 };
}

/**
 * Returns the YYYY-MM key for the given year/monthIndex.
 */
export function monthKey(year: number, monthIndex: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, "0")}`;
}

/**
 * Returns the full date range covering a calendar month in ET.
 */
export function monthDateRange(year: number, monthIndex: number): DateRange {
  const daysInMonth = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
  const mm = String(monthIndex + 1).padStart(2, "0");
  return {
    dateFrom: `${year}-${mm}-01`,
    dateTo: `${year}-${mm}-${String(daysInMonth).padStart(2, "0")}`,
  };
}

/**
 * Shifts a year/monthIndex pair by `delta` months (can be negative).
 */
export function shiftMonth(
  year: number,
  monthIndex: number,
  delta: number,
): { year: number; monthIndex: number } {
  const total = year * 12 + monthIndex + delta;
  return {
    year: Math.floor(total / 12),
    monthIndex: ((total % 12) + 12) % 12,
  };
}

/**
 * Computes the date range for a named window, anchored to `now` in ET.
 * Returns YYYY-MM-DD strings suitable for the events API.
 */
export function windowDateRange(
  window: DateWindow,
  now: Date = new Date(),
): DateRange {
  const today = etDateKey(now);
  const todayDow = etDayOfWeek(now);

  if (window === "tonight") {
    return { dateFrom: today, dateTo: today };
  }

  if (window === "week") {
    return { dateFrom: today, dateTo: addDaysToKey(today, 6) };
  }

  // weekend: upcoming Fri/Sat/Sun. If today is already Fri/Sat/Sun,
  // start from today. Otherwise start from this week's Friday.
  const daysUntilFriday =
    todayDow >= 5 || todayDow === 0 ? 0 : 5 - todayDow;
  const weekendStart =
    todayDow === 0
      ? today
      : addDaysToKey(today, daysUntilFriday);
  const daysUntilSunday =
    todayDow === 0 ? 0 : 7 - todayDow;
  const weekendEnd = addDaysToKey(today, daysUntilSunday);
  return { dateFrom: weekendStart, dateTo: weekendEnd };
}

/**
 * Groups events into sticky-header buckets:
 * - "Tonight" (today in ET)
 * - "This weekend" (Fri/Sat/Sun of the current week, excluding tonight)
 * - "Next 7 days" (the rest of the next 7 days)
 * - "Week of Mon DD" for each calendar week beyond
 *
 * Events without a `starts_at` are dropped from the grouped view —
 * callers that care about TBD dates should surface them separately.
 */
export function bucketizeEvents(
  events: EventSummary[],
  now: Date = new Date(),
): DateBucket[] {
  const today = etDateKey(now);
  const todayDow = etDayOfWeek(now);
  const weekendKeys = new Set<string>();
  if (todayDow !== 0) {
    const friOffset = todayDow >= 5 ? 0 : 5 - todayDow;
    weekendKeys.add(addDaysToKey(today, friOffset));
    weekendKeys.add(addDaysToKey(today, friOffset + 1));
    weekendKeys.add(addDaysToKey(today, friOffset + 2));
  } else {
    weekendKeys.add(today);
  }
  weekendKeys.delete(today);

  const sevenDayEnd = addDaysToKey(today, 6);

  const buckets = new Map<string, DateBucket>();
  const ensure = (key: string, label: string): DateBucket => {
    const existing = buckets.get(key);
    if (existing) return existing;
    const created: DateBucket = { key, label, events: [] };
    buckets.set(key, created);
    return created;
  };

  const order: string[] = [];
  const register = (key: string, label: string): DateBucket => {
    if (!buckets.has(key)) order.push(key);
    return ensure(key, label);
  };

  for (const event of events) {
    if (!event.starts_at) continue;
    const eventDate = new Date(event.starts_at);
    if (Number.isNaN(eventDate.getTime())) continue;
    const key = etDateKey(eventDate);

    if (key === today) {
      register("tonight", "Tonight").events.push(event);
      continue;
    }
    if (weekendKeys.has(key)) {
      register("weekend", "This weekend").events.push(event);
      continue;
    }
    if (key <= sevenDayEnd) {
      register("next7", "Next 7 days").events.push(event);
      continue;
    }

    // Beyond seven days: bucket by calendar week (Monday-start).
    const dow = etDayOfWeek(eventDate);
    const offsetToMonday = (dow + 6) % 7;
    const weekKey = addDaysToKey(key, -offsetToMonday);
    const label = `Week of ${headerFromKey(weekKey)}`;
    register(`week:${weekKey}`, label).events.push(event);
  }

  return order.map((k) => buckets.get(k)!);
}
