/**
 * Month-grid calendar view of upcoming shows.
 *
 * Renders a 7-column month grid with per-day show counts. Days with
 * events link to `/events?date=YYYY-MM-DD`, which narrows the list
 * view to that single day. Prev/next buttons link to the previous
 * and next month — navigation is URL-driven so SSR can fetch the
 * correct month's events for each page view.
 *
 * Server component — receives `events` for the displayed month and
 * an explicit `year` / `monthIndex` so day-cell keys align with the
 * data. Expected to be wrapped by a page that handles `?month=YYYY-MM`.
 */

import Link from "next/link";

import { etDateKey } from "@/lib/dates";
import type { EventSummary } from "@/types";

interface CalendarViewProps {
  events: EventSummary[];
  year: number;
  monthIndex: number;
  todayKey: string;
  citySlug: string | null;
  prevMonthHref: string;
  nextMonthHref: string;
}

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const MONTH_FORMAT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "long",
  year: "numeric",
});

interface CalendarCell {
  key: string;
  day: number;
  inMonth: boolean;
  count: number;
  isToday: boolean;
}

function countsByDate(events: EventSummary[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const event of events) {
    if (!event.starts_at) continue;
    const d = new Date(event.starts_at);
    if (Number.isNaN(d.getTime())) continue;
    const key = etDateKey(d);
    map.set(key, (map.get(key) ?? 0) + 1);
  }
  return map;
}

function buildMonthCells(
  year: number,
  monthIndex: number,
  counts: Map<string, number>,
  todayKey: string,
): CalendarCell[] {
  const firstOfMonth = new Date(Date.UTC(year, monthIndex, 1));
  const firstDow = firstOfMonth.getUTCDay();
  const daysInMonth = new Date(
    Date.UTC(year, monthIndex + 1, 0),
  ).getUTCDate();
  const prevMonthDays = new Date(Date.UTC(year, monthIndex, 0)).getUTCDate();

  const cells: CalendarCell[] = [];

  for (let i = firstDow - 1; i >= 0; i--) {
    const day = prevMonthDays - i;
    const prevMonthIndex = monthIndex === 0 ? 11 : monthIndex - 1;
    const prevYear = monthIndex === 0 ? year - 1 : year;
    const key = `${prevYear}-${String(prevMonthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    cells.push({
      key,
      day,
      inMonth: false,
      count: counts.get(key) ?? 0,
      isToday: key === todayKey,
    });
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const key = `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    cells.push({
      key,
      day,
      inMonth: true,
      count: counts.get(key) ?? 0,
      isToday: key === todayKey,
    });
  }

  while (cells.length % 7 !== 0) {
    const next = cells.length - firstDow - daysInMonth + 1;
    const nextMonthIndex = monthIndex === 11 ? 0 : monthIndex + 1;
    const nextYear = monthIndex === 11 ? year + 1 : year;
    const key = `${nextYear}-${String(nextMonthIndex + 1).padStart(2, "0")}-${String(next).padStart(2, "0")}`;
    cells.push({
      key,
      day: next,
      inMonth: false,
      count: counts.get(key) ?? 0,
      isToday: key === todayKey,
    });
  }

  return cells;
}

function buildDayHref(dateKey: string, citySlug: string | null): string {
  const params = new URLSearchParams();
  if (citySlug) params.set("city", citySlug);
  params.set("date", dateKey);
  return `/events?${params.toString()}`;
}

export default function CalendarView({
  events,
  year,
  monthIndex,
  todayKey,
  citySlug,
  prevMonthHref,
  nextMonthHref,
}: CalendarViewProps): JSX.Element {
  const counts = countsByDate(events);
  const cells = buildMonthCells(year, monthIndex, counts, todayKey);
  const displayLabel = MONTH_FORMAT.format(
    new Date(Date.UTC(year, monthIndex, 15)),
  );
  const totalThisMonth = cells
    .filter((c) => c.inMonth)
    .reduce((acc, c) => acc + c.count, 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <h2 className="text-lg font-semibold">{displayLabel}</h2>
          <span className="text-sm text-muted">
            {totalThisMonth} {totalThisMonth === 1 ? "show" : "shows"}
          </span>
        </div>
        <div className="flex gap-1">
          <Link
            href={prevMonthHref}
            aria-label="Previous month"
            className="rounded-md border border-border px-3 py-1 text-sm hover:border-accent hover:text-accent"
          >
            ←
          </Link>
          <Link
            href={nextMonthHref}
            aria-label="Next month"
            className="rounded-md border border-border px-3 py-1 text-sm hover:border-accent hover:text-accent"
          >
            →
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-border bg-border">
        {WEEKDAY_LABELS.map((label) => (
          <div
            key={label}
            className="bg-surface px-2 py-2 text-center text-xs font-semibold uppercase tracking-wide text-muted"
          >
            {label}
          </div>
        ))}
        {cells.map((cell) => {
          const hasEvents = cell.count > 0;
          const base =
            "flex aspect-square flex-col items-start justify-between p-2 text-sm sm:aspect-[4/3]";
          const tone = cell.inMonth ? "bg-bg-white" : "bg-bg-surface/60";
          const textColor = cell.inMonth
            ? cell.isToday
              ? "text-blush-accent font-semibold"
              : "text-text-primary"
            : "text-muted";

          const dayNumber = (
            <span className={`text-xs ${textColor}`}>{cell.day}</span>
          );

          if (hasEvents && cell.inMonth) {
            return (
              <Link
                key={cell.key}
                href={buildDayHref(cell.key, citySlug)}
                className={`${base} ${tone} transition hover:bg-green-soft/40`}
              >
                {dayNumber}
                <span className="inline-flex items-center gap-1 rounded-full bg-green-primary px-2 py-0.5 text-xs font-semibold text-text-inverse">
                  {cell.count}
                </span>
              </Link>
            );
          }

          return (
            <div key={cell.key} className={`${base} ${tone}`}>
              {dayNumber}
            </div>
          );
        })}
      </div>
    </div>
  );
}
