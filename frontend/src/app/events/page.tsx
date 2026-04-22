/**
 * Events index — `/events` (server-side rendered).
 *
 * Paginated list of upcoming concerts across the DMV. Accepts:
 * - `?city=<slug>` to narrow to a single city
 * - `?window=tonight|weekend|week` to restrict to a date window
 * - `?page=<n>` for pagination
 *
 * Events are grouped into sticky-header date buckets ("Tonight",
 * "This weekend", "Next 7 days", then week-by-week) so the calendar
 * feel is visible at a glance.
 */

import Link from "next/link";
import type { Metadata } from "next";

import CalendarView from "@/components/events/CalendarView";
import EventCard from "@/components/events/EventCard";
import EmptyState from "@/components/ui/EmptyState";
import WindowFilterChips from "@/components/events/WindowFilterChips";
import BreadcrumbStructuredData from "@/components/seo/BreadcrumbStructuredData";
import { getCityBySlug } from "@/lib/api/cities";
import { listEvents } from "@/lib/api/events";
import {
  bucketizeEvents,
  etDateKey,
  etMonthOf,
  monthDateRange,
  monthKey,
  parseMonthKey,
  shiftMonth,
  windowDateRange,
  type DateWindow,
} from "@/lib/dates";
import {
  absolutePageUrl,
  buildEventsIndexMetadata,
} from "@/lib/metadata";
import type { City, EventSummary, Paginated } from "@/types";

// Disables ISR caching: "Tonight" bucketing calls `new Date()` at render
// time, and a cached HTML page pins yesterday's date until the next hit.
// Force-dynamic keeps the clock honest without moving bucketization to the client.
export const dynamic = "force-dynamic";

const PER_PAGE = 48;

type EventsView = "list" | "calendar";

interface EventsPageProps {
  searchParams: {
    city?: string;
    page?: string;
    window?: string;
    view?: string;
    date?: string;
    month?: string;
  };
}

function parseView(value: string | undefined): EventsView {
  return value === "calendar" ? "calendar" : "list";
}

function parseDate(value: string | undefined): string | null {
  if (!value) return null;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

const CALENDAR_PAGE_CAP = 5;
const CALENDAR_PER_PAGE = 100;

async function fetchEventsInRange(
  params: {
    region?: "DMV";
    cityId?: string;
    dateFrom: string;
    dateTo: string;
  },
): Promise<EventSummary[]> {
  const collected: EventSummary[] = [];
  for (let page = 1; page <= CALENDAR_PAGE_CAP; page++) {
    const res = await listEvents({
      region: params.region,
      cityId: params.cityId,
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      page,
      perPage: CALENDAR_PER_PAGE,
      revalidateSeconds: 300,
    });
    collected.push(...res.data);
    if (!res.meta.has_next) break;
  }
  return collected;
}

function parsePage(value: string | undefined): number {
  const n = value ? Number.parseInt(value, 10) : 1;
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

function parseWindow(value: string | undefined): DateWindow | null {
  if (value === "tonight" || value === "weekend" || value === "week") {
    return value;
  }
  return null;
}

async function loadCity(slug: string | undefined): Promise<City | null> {
  if (!slug) return null;
  try {
    return await getCityBySlug(slug, 300);
  } catch {
    return null;
  }
}

export async function generateMetadata({
  searchParams,
}: EventsPageProps): Promise<Metadata> {
  const city = await loadCity(searchParams.city);
  return buildEventsIndexMetadata(city?.name ?? null);
}

export default async function EventsPage({ searchParams }: EventsPageProps) {
  const page = parsePage(searchParams.page);
  const city = await loadCity(searchParams.city);
  const activeWindow = parseWindow(searchParams.window);
  const activeView = parseView(searchParams.view);
  const specificDate = parseDate(searchParams.date);
  const windowRange = activeWindow ? windowDateRange(activeWindow) : null;

  const now = new Date();
  const todayKey = etDateKey(now);
  const requestedMonth = parseMonthKey(searchParams.month);
  const currentMonth = etMonthOf(now);
  const calendarMonth = requestedMonth ?? currentMonth;

  let results: Paginated<EventSummary> = {
    data: [],
    meta: { total: 0, page, per_page: PER_PAGE, has_next: false },
  };
  let calendarEvents: EventSummary[] = [];

  if (activeView === "calendar") {
    const range = monthDateRange(calendarMonth.year, calendarMonth.monthIndex);
    try {
      calendarEvents = await fetchEventsInRange({
        region: city ? undefined : "DMV",
        cityId: city?.id,
        dateFrom: range.dateFrom,
        dateTo: range.dateTo,
      });
    } catch {
      calendarEvents = [];
    }
  } else {
    const dateFrom = specificDate ?? windowRange?.dateFrom;
    const dateTo = specificDate ?? windowRange?.dateTo;
    try {
      results = await listEvents({
        region: city ? undefined : "DMV",
        cityId: city?.id,
        dateFrom,
        dateTo,
        page,
        perPage: PER_PAGE,
        revalidateSeconds: 300,
      });
    } catch {
      results = {
        data: [],
        meta: { total: 0, page, per_page: PER_PAGE, has_next: false },
      };
    }
  }

  const heading = city ? `Concerts in ${city.name}` : "Concerts across the DMV";
  const buckets = bucketizeEvents(results.data);

  const prevMonth = shiftMonth(
    calendarMonth.year,
    calendarMonth.monthIndex,
    -1,
  );
  const nextMonth = shiftMonth(
    calendarMonth.year,
    calendarMonth.monthIndex,
    1,
  );
  const calendarPrevHref = buildEventsUrl({
    citySlug: city?.slug ?? null,
    windowValue: null,
    view: "calendar",
    month: monthKey(prevMonth.year, prevMonth.monthIndex),
  });
  const calendarNextHref = buildEventsUrl({
    citySlug: city?.slug ?? null,
    windowValue: null,
    view: "calendar",
    month: monthKey(nextMonth.year, nextMonth.monthIndex),
  });

  return (
    <>
      <BreadcrumbStructuredData
        items={[
          { name: "Home", url: absolutePageUrl("/") },
          { name: "Events", url: absolutePageUrl("/events") },
        ]}
      />

      <section className="flex flex-col gap-4 pb-6 pt-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <h1 className="text-2xl font-bold sm:text-3xl">{heading}</h1>
            <p className="text-sm text-muted">
              {specificDate
                ? `Showing shows on ${specificDate}.`
                : results.meta.total > 0
                  ? `Showing ${results.data.length} of ${results.meta.total} upcoming shows.`
                  : "Updated nightly from venue websites and Ticketmaster."}
            </p>
          </div>
          <ViewToggle
            active={activeView}
            citySlug={city?.slug ?? null}
            windowValue={activeWindow}
            date={specificDate}
          />
        </div>
        {activeView === "list" ? (
          <WindowFilterChips
            active={activeWindow}
            citySlug={city?.slug ?? null}
          />
        ) : null}
        {specificDate ? (
          <Link
            href={buildEventsUrl({
              citySlug: city?.slug ?? null,
              windowValue: activeWindow,
              view: activeView,
            })}
            className="inline-flex w-fit items-center gap-1 text-sm text-accent hover:underline"
          >
            ← Clear date filter
          </Link>
        ) : null}
      </section>

      {activeView === "calendar" ? (
        <CalendarView
          events={calendarEvents}
          year={calendarMonth.year}
          monthIndex={calendarMonth.monthIndex}
          todayKey={todayKey}
          citySlug={city?.slug ?? null}
          prevMonthHref={calendarPrevHref}
          nextMonthHref={calendarNextHref}
        />
      ) : results.data.length === 0 ? (
        <EmptyState
          title="No shows match these filters"
          description="Try switching to a different city, clearing the date window, or view the whole DMV calendar."
        >
          <Link
            href="/events"
            className="mt-2 text-sm font-medium text-accent hover:underline"
          >
            View all DMV events →
          </Link>
        </EmptyState>
      ) : (
        <>
          <div className="flex flex-col gap-10">
            {buckets.map((bucket) => (
              <section key={bucket.key} className="flex flex-col gap-4">
                <h2 className="sticky top-0 z-10 -mx-4 bg-background/95 px-4 py-2 text-lg font-semibold backdrop-blur sm:-mx-6 sm:px-6">
                  {bucket.label}
                  <span className="ml-2 text-sm font-normal text-muted">
                    ({bucket.events.length})
                  </span>
                </h2>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {bucket.events.map((event) => (
                    <EventCard key={event.id} event={event} />
                  ))}
                </div>
              </section>
            ))}
          </div>
          {specificDate ? null : (
            <Pagination
              page={results.meta.page}
              hasNext={results.meta.has_next}
              citySlug={city?.slug ?? null}
              windowValue={activeWindow}
            />
          )}
        </>
      )}
    </>
  );
}

interface EventsUrlParts {
  citySlug: string | null;
  windowValue: DateWindow | null;
  view: EventsView;
  date?: string | null;
  page?: number;
  month?: string | null;
}

function buildEventsUrl(parts: EventsUrlParts): string {
  const params = new URLSearchParams();
  if (parts.citySlug) params.set("city", parts.citySlug);
  if (parts.windowValue) params.set("window", parts.windowValue);
  if (parts.view === "calendar") params.set("view", "calendar");
  if (parts.date) params.set("date", parts.date);
  if (parts.month) params.set("month", parts.month);
  if (parts.page && parts.page > 1) params.set("page", String(parts.page));
  const q = params.toString();
  return q ? `/events?${q}` : "/events";
}

function ViewToggle({
  active,
  citySlug,
  windowValue,
  date,
}: {
  active: EventsView;
  citySlug: string | null;
  windowValue: DateWindow | null;
  date: string | null;
}): JSX.Element {
  const options: { value: EventsView; label: string }[] = [
    { value: "list", label: "List" },
    { value: "calendar", label: "Calendar" },
  ];
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-border">
      {options.map((opt) => {
        const isActive = active === opt.value;
        const href = buildEventsUrl({
          citySlug,
          windowValue,
          view: opt.value,
          date,
        });
        const classes = isActive
          ? "bg-green-primary text-text-inverse"
          : "bg-bg-white text-text-primary hover:bg-green-soft/40";
        return (
          <Link
            key={opt.value}
            href={href}
            aria-pressed={isActive}
            className={`px-3 py-1.5 text-sm font-medium ${classes}`}
          >
            {opt.label}
          </Link>
        );
      })}
    </div>
  );
}

function Pagination({
  page,
  hasNext,
  citySlug,
  windowValue,
}: {
  page: number;
  hasNext: boolean;
  citySlug: string | null;
  windowValue: DateWindow | null;
}) {
  const qs = (p: number): string =>
    buildEventsUrl({
      citySlug,
      windowValue,
      view: "list",
      page: p,
    });
  return (
    <nav
      aria-label="Pagination"
      className="flex items-center justify-between pt-8"
    >
      {page > 1 ? (
        <Link
          href={qs(page - 1)}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
        >
          ← Previous
        </Link>
      ) : (
        <span />
      )}
      <span className="text-sm text-muted">Page {page}</span>
      {hasNext ? (
        <Link
          href={qs(page + 1)}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
        >
          Next →
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}
