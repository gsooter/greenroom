/**
 * Single For-You card: event tile plus the chips explaining why we
 * surfaced it ("You listen to X", "Because you like Indie Rock", "You've
 * saved shows at Black Cat").
 *
 * Reason chips are deduped on a stable identity key (artist name, genre
 * slug, venue name, or label fallback) and capped at three so the chip
 * row never wraps past the card width on the narrowest mobile target.
 */

import EventCard from "@/components/events/EventCard";
import type { Recommendation, RecommendationMatchReason } from "@/types";

interface RecommendationCardProps {
  recommendation: Recommendation;
}

export default function RecommendationCard({
  recommendation,
}: RecommendationCardProps): JSX.Element {
  return (
    <div className="flex flex-col gap-2">
      <EventCard event={recommendation.event} />
      <ReasonChips reasons={recommendation.match_reasons} />
    </div>
  );
}

const MAX_REASONS = 3;

function ReasonChips({
  reasons,
}: {
  reasons: RecommendationMatchReason[];
}): JSX.Element | null {
  if (!reasons || reasons.length === 0) return null;
  const unique: RecommendationMatchReason[] = [];
  const seen = new Set<string>();
  for (const reason of reasons) {
    const key = (
      reason.artist_name ??
      reason.genre_slug ??
      reason.genre ??
      reason.venue_name ??
      reason.label
    ).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(reason);
    if (unique.length >= MAX_REASONS) break;
  }

  return (
    <ul className="flex flex-wrap gap-2">
      {unique.map((reason) => (
        <li
          key={`${reason.scorer}:${reason.kind}:${reason.label}`}
          className="rounded-full bg-blush-soft px-3 py-1 text-xs font-medium text-blush-accent"
        >
          {reason.label}
        </li>
      ))}
    </ul>
  );
}
