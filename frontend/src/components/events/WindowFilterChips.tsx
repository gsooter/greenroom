/**
 * Date-window chip row rendered above the events list on `/events`.
 *
 * Server component — each chip is a plain `<Link>` that toggles the
 * `window` query param. The active chip highlights in forest green;
 * inactive chips are neutral. Selecting a chip while it's already
 * active clears the filter (link points back at the no-window URL).
 */

import Link from "next/link";

import type { DateWindow } from "@/lib/dates";

interface WindowFilterChipsProps {
  active: DateWindow | null;
  citySlug: string | null;
}

const OPTIONS: { value: DateWindow; label: string }[] = [
  { value: "tonight", label: "Tonight" },
  { value: "weekend", label: "This weekend" },
  { value: "week", label: "Next 7 days" },
];

function buildHref(
  value: DateWindow | null,
  citySlug: string | null,
): string {
  const params = new URLSearchParams();
  if (citySlug) params.set("city", citySlug);
  if (value) params.set("window", value);
  const qs = params.toString();
  return qs ? `/events?${qs}` : "/events";
}

export default function WindowFilterChips({
  active,
  citySlug,
}: WindowFilterChipsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {OPTIONS.map((opt) => {
        const isActive = active === opt.value;
        const href = buildHref(isActive ? null : opt.value, citySlug);
        const classes = isActive
          ? "bg-green-primary text-text-inverse border-green-primary"
          : "bg-surface text-foreground border-border hover:border-accent hover:text-accent";
        return (
          <Link
            key={opt.value}
            href={href}
            aria-pressed={isActive}
            className={`rounded-full border px-3 py-1 text-sm font-medium transition ${classes}`}
          >
            {opt.label}
          </Link>
        );
      })}
    </div>
  );
}
