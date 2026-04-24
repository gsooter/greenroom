/**
 * Interactive day cell for the month-grid calendar.
 *
 * Rendered by `CalendarView` for days that have at least one show in
 * the current month. On hover (or keyboard focus), it reveals a small
 * popover listing every show scheduled for that day, grouped by genre
 * bucket. The list items link directly to the event detail page so the
 * popover doubles as a quick-jump menu.
 *
 * Client component — `CalendarView` stays server-side so SSR keeps its
 * rich rendering. The popover is portaled into ``document.body`` and
 * positioned with ``getBoundingClientRect`` so it is not clipped by the
 * grid's ``overflow-hidden``.
 */

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  pinColorVariable,
  type MapPinColor,
} from "@/lib/genre-colors";
import type { EventSummary } from "@/types";

interface DayEntry {
  event: EventSummary;
  bucket: MapPinColor;
}

interface CalendarDayCellProps {
  href: string;
  dayNumber: number;
  dayNumberClass: string;
  cellClass: string;
  toneClass: string;
  chips: Array<[MapPinColor, number]>;
  entries: DayEntry[];
  bucketLabels: Readonly<Record<MapPinColor, string>>;
  bucketOrder: readonly MapPinColor[];
}

const TIME_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "numeric",
  minute: "2-digit",
});

function formatShowTime(startsAt: string | null): string {
  if (!startsAt) return "";
  const d = new Date(startsAt);
  if (Number.isNaN(d.getTime())) return "";
  return TIME_FMT.format(d);
}

/**
 * Day cell with a hover popover listing that day's shows.
 */
export default function CalendarDayCell({
  href,
  dayNumber,
  dayNumberClass,
  cellClass,
  toneClass,
  chips,
  entries,
  bucketLabels,
  bucketOrder,
}: CalendarDayCellProps): JSX.Element {
  const anchorRef = useRef<HTMLAnchorElement | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [position, setPosition] = useState<{
    top: number;
    left: number;
    placement: "below" | "above";
  } | null>(null);

  useEffect(() => setMounted(true), []);

  const updatePosition = (): void => {
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    // Approximate popover height so we can flip above when the cell is
    // near the bottom of the viewport. The real measurement happens
    // after mount via the ResizeObserver below, but a static estimate
    // covers the first paint on every cell.
    const estimatedHeight = 240;
    const spaceBelow = window.innerHeight - rect.bottom;
    const placement: "below" | "above" =
      spaceBelow < estimatedHeight + 16 && rect.top > estimatedHeight
        ? "above"
        : "below";
    const top =
      placement === "below" ? rect.bottom + 6 : rect.top - 6;
    setPosition({
      top,
      left: rect.left + rect.width / 2,
      placement,
    });
  };

  const cancelClose = (): void => {
    if (closeTimerRef.current !== null) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const handleEnter = (): void => {
    cancelClose();
    updatePosition();
    setOpen(true);
  };

  // Delay close so the cursor can cross the small gap between cell
  // and popover without dismissing. The popover's own onMouseEnter
  // cancels the pending timer.
  const handleLeave = (): void => {
    cancelClose();
    closeTimerRef.current = setTimeout(() => setOpen(false), 120);
  };

  useEffect(() => cancelClose, []);

  useEffect(() => {
    if (!open) return;
    const onScroll = (): void => updatePosition();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [open]);

  // Group entries by bucket in the declared bucket order so the popover
  // mirrors the chip order on the cell itself.
  const grouped = bucketOrder
    .map((bucket) => ({
      bucket,
      items: entries.filter((e) => e.bucket === bucket),
    }))
    .filter((g) => g.items.length > 0);

  const popover =
    open && mounted && position
      ? createPortal(
          <div
            role="tooltip"
            onMouseEnter={handleEnter}
            onMouseLeave={handleLeave}
            className={`fixed z-50 flex max-h-[min(22rem,80vh)] w-64 -translate-x-1/2 flex-col rounded-lg border border-border bg-bg-white text-sm shadow-lg ${
              position.placement === "above" ? "-translate-y-full" : ""
            }`}
            style={{ top: position.top, left: position.left }}
          >
            <ul className="flex flex-col gap-2 overflow-y-auto p-3">
              {grouped.map(({ bucket, items }) => (
                <li key={bucket} className="flex flex-col gap-1">
                  <div className="sticky top-0 flex items-center gap-1.5 bg-bg-white pb-1 text-[10px] font-semibold uppercase tracking-wide text-text-secondary">
                    <span
                      aria-hidden
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ backgroundColor: pinColorVariable(bucket) }}
                    />
                    {bucketLabels[bucket]}
                  </div>
                  <ul className="flex flex-col gap-0.5 pl-3">
                    {items.map(({ event }) => (
                      <li key={event.id}>
                        <Link
                          href={`/events/${event.slug}`}
                          className="block truncate rounded px-1 py-0.5 text-xs text-text-primary hover:bg-green-soft/40 hover:text-accent"
                        >
                          <span className="text-text-secondary">
                            {formatShowTime(event.starts_at)}
                          </span>{" "}
                          <span className="font-medium">{event.title}</span>
                          {event.venue ? (
                            <span className="text-text-secondary">
                              {" · "}
                              {event.venue.name}
                            </span>
                          ) : null}
                        </Link>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <Link
        ref={anchorRef}
        href={href}
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
        onFocus={handleEnter}
        onBlur={handleLeave}
        className={`${cellClass} ${toneClass} transition hover:bg-green-soft/40`}
      >
        <span className={`text-xs ${dayNumberClass}`}>{dayNumber}</span>
        <div className="flex flex-wrap gap-1">
          {chips.map(([bucket, n]) => (
            <span
              key={bucket}
              title={`${bucketLabels[bucket]} · ${n} ${n === 1 ? "show" : "shows"}`}
              className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold text-text-inverse"
              style={{ backgroundColor: pinColorVariable(bucket) }}
            >
              {n}
            </span>
          ))}
        </div>
      </Link>
      {popover}
    </>
  );
}
