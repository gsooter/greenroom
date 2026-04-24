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

import CalendarDayCell from "@/components/events/CalendarDayCell";
import { etDateKey } from "@/lib/dates";
import {
  pinColorForGenres,
  pinColorVariable,
  type MapPinColor,
} from "@/lib/genre-colors";
import type { EventSummary } from "@/types";

const BUCKET_ORDER: readonly MapPinColor[] = [
  "green",
  "blush",
  "amber",
  "coral",
  "gold",
  "navy",
];

const BUCKET_LABEL: Readonly<Record<MapPinColor, string>> = {
  green: "Indie & rock",
  blush: "Pop & folk",
  amber: "Electronic",
  coral: "Hip-hop",
  gold: "Jazz & soul",
  navy: "Other",
};

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

interface DayEntry {
  event: EventSummary;
  bucket: MapPinColor;
}

interface DayBuckets {
  count: number;
  buckets: Map<MapPinColor, number>;
  entries: DayEntry[];
}

interface CalendarCell {
  key: string;
  day: number;
  inMonth: boolean;
  count: number;
  buckets: Map<MapPinColor, number>;
  entries: DayEntry[];
  isToday: boolean;
}

function bucketsByDate(events: EventSummary[]): Map<string, DayBuckets> {
  const map = new Map<string, DayBuckets>();
  for (const event of events) {
    if (!event.starts_at) continue;
    const d = new Date(event.starts_at);
    if (Number.isNaN(d.getTime())) continue;
    const key = etDateKey(d);
    const bucket = pinColorForGenres(event.genres);
    const entry = map.get(key) ?? {
      count: 0,
      buckets: new Map(),
      entries: [] as DayEntry[],
    };
    entry.count += 1;
    entry.buckets.set(bucket, (entry.buckets.get(bucket) ?? 0) + 1);
    entry.entries.push({ event, bucket });
    map.set(key, entry);
  }
  return map;
}

function orderedBuckets(
  buckets: Map<MapPinColor, number>,
): Array<[MapPinColor, number]> {
  return BUCKET_ORDER.filter((b) => buckets.has(b)).map(
    (b) => [b, buckets.get(b) ?? 0] as [MapPinColor, number],
  );
}

function buildMonthCells(
  year: number,
  monthIndex: number,
  byDate: Map<string, DayBuckets>,
  todayKey: string,
): CalendarCell[] {
  const firstOfMonth = new Date(Date.UTC(year, monthIndex, 1));
  const firstDow = firstOfMonth.getUTCDay();
  const daysInMonth = new Date(
    Date.UTC(year, monthIndex + 1, 0),
  ).getUTCDate();
  const prevMonthDays = new Date(Date.UTC(year, monthIndex, 0)).getUTCDate();

  const cells: CalendarCell[] = [];

  const readDay = (key: string): DayBuckets => {
    const hit = byDate.get(key);
    return hit ?? { count: 0, buckets: new Map(), entries: [] };
  };

  for (let i = firstDow - 1; i >= 0; i--) {
    const day = prevMonthDays - i;
    const prevMonthIndex = monthIndex === 0 ? 11 : monthIndex - 1;
    const prevYear = monthIndex === 0 ? year - 1 : year;
    const key = `${prevYear}-${String(prevMonthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const dayData = readDay(key);
    cells.push({
      key,
      day,
      inMonth: false,
      count: dayData.count,
      buckets: dayData.buckets,
      entries: dayData.entries,
      isToday: key === todayKey,
    });
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const key = `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const dayData = readDay(key);
    cells.push({
      key,
      day,
      inMonth: true,
      count: dayData.count,
      buckets: dayData.buckets,
      entries: dayData.entries,
      isToday: key === todayKey,
    });
  }

  while (cells.length % 7 !== 0) {
    const next = cells.length - firstDow - daysInMonth + 1;
    const nextMonthIndex = monthIndex === 11 ? 0 : monthIndex + 1;
    const nextYear = monthIndex === 11 ? year + 1 : year;
    const key = `${nextYear}-${String(nextMonthIndex + 1).padStart(2, "0")}-${String(next).padStart(2, "0")}`;
    const dayData = readDay(key);
    cells.push({
      key,
      day: next,
      inMonth: false,
      count: dayData.count,
      buckets: dayData.buckets,
      entries: dayData.entries,
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
  const byDate = bucketsByDate(events);
  const cells = buildMonthCells(year, monthIndex, byDate, todayKey);
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

      <ul
        aria-label="Genre color key"
        className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-secondary"
      >
        {BUCKET_ORDER.map((bucket) => (
          <li key={bucket} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: pinColorVariable(bucket) }}
            />
            {BUCKET_LABEL[bucket]}
          </li>
        ))}
      </ul>

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
            const chips = orderedBuckets(cell.buckets);
            return (
              <CalendarDayCell
                key={cell.key}
                href={buildDayHref(cell.key, citySlug)}
                dayNumber={cell.day}
                dayNumberClass={textColor}
                cellClass={base}
                toneClass={tone}
                chips={chips}
                entries={cell.entries}
                bucketLabels={BUCKET_LABEL}
                bucketOrder={BUCKET_ORDER}
              />
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
