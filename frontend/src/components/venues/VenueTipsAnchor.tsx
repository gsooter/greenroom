/**
 * Hero-area pill linking down to the venue tips section.
 *
 * The tips widget lives below upcoming shows, which on a busy venue
 * page pushes it well below the fold. This pill surfaces how many tips
 * exist and jumps the reader straight to them so community knowledge
 * isn't hidden behind a scroll.
 */

"use client";

import { useEffect, useState } from "react";

import { listVenueComments } from "@/lib/api/venue-comments";
import { getGuestSessionId } from "@/lib/guest-session";
import { useAuth } from "@/lib/auth";

interface Props {
  slug: string;
}

/**
 * Renders a compact anchor-button showing the current tip count.
 *
 * Starts in a loading state, fetches the count once on mount, and
 * switches to a "Be the first" label when the venue has no tips yet so
 * the pill still nudges users toward the section.
 *
 * @param slug - Venue slug used to fetch the comment count.
 * @returns A link styled as a pill with a chat glyph.
 */
export default function VenueTipsAnchor({ slug }: Props): JSX.Element {
  const { token, isAuthenticated } = useAuth();
  const [count, setCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const sessionId = isAuthenticated ? undefined : getGuestSessionId();
    void listVenueComments(slug, token, { sessionId })
      .then((res) => {
        if (!cancelled) setCount(res.meta.count);
      })
      .catch(() => {
        if (!cancelled) setCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, token, isAuthenticated]);

  const label = formatLabel(count);

  return (
    <a
      href="#tips"
      className="inline-flex w-fit items-center gap-1.5 rounded-full border border-green-primary bg-green-soft px-3 py-1 text-xs font-medium text-green-dark transition hover:bg-green-primary hover:text-text-inverse"
    >
      <span aria-hidden="true">💬</span>
      <span>{label}</span>
      <span aria-hidden="true">↓</span>
    </a>
  );
}

/**
 * Format the pill label for a given comment count.
 *
 * @param count - Current comment count, or null while loading.
 * @returns User-facing label text.
 */
function formatLabel(count: number | null): string {
  if (count === null) return "Tips from the community";
  if (count === 0) return "Be the first to leave a tip";
  if (count === 1) return "1 tip from the community";
  return `${count} tips from the community`;
}
