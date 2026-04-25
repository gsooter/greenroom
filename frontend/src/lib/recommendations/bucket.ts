/**
 * Time-bucketing for the For-You page.
 *
 * Groups a score-sorted recommendation list into three coarse sections —
 * Tonight, This week, Coming up — so the highest-priority shows are the
 * first thing a returning user sees. Anything missing a `starts_at`
 * lands in Coming up so it never blocks rendering.
 *
 * Buckets are anchored to America/New_York; a viewer in California
 * doesn't see tomorrow's DC shows labeled "Tonight".
 */

import { addDaysToKey, etDateKey } from "@/lib/dates";
import type { Recommendation } from "@/types";

export type RecommendationBucketKey = "tonight" | "this_week" | "coming_up";

export interface RecommendationBucket {
  key: RecommendationBucketKey;
  label: string;
  recommendations: Recommendation[];
}

const BUCKET_LABEL: Record<RecommendationBucketKey, string> = {
  tonight: "Tonight",
  this_week: "This week",
  coming_up: "Coming up",
};

/**
 * Splits a recommendation list into Tonight / This week / Coming up.
 *
 * Order within each bucket is preserved from the input — callers
 * already pass score-desc data, so the highest match in each window
 * naturally sorts first.
 *
 * @param recs - Recommendations as returned by the API.
 * @param now - Reference moment used to compute "today" in ET. Defaults
 *     to wall-clock time; tests pass an explicit value.
 *
 * @returns The non-empty buckets in fixed order (Tonight → Coming up).
 *     Empty buckets are dropped so the UI doesn't render empty headers.
 */
export function bucketizeRecommendations(
  recs: Recommendation[],
  now: Date = new Date(),
): RecommendationBucket[] {
  const today = etDateKey(now);
  const sevenDayEnd = addDaysToKey(today, 6);

  const tonight: Recommendation[] = [];
  const thisWeek: Recommendation[] = [];
  const comingUp: Recommendation[] = [];

  for (const rec of recs) {
    const startsAt = rec.event.starts_at;
    if (!startsAt) {
      comingUp.push(rec);
      continue;
    }
    const date = new Date(startsAt);
    if (Number.isNaN(date.getTime())) {
      comingUp.push(rec);
      continue;
    }
    const key = etDateKey(date);
    if (key < today) {
      continue;
    }
    if (key === today) {
      tonight.push(rec);
    } else if (key <= sevenDayEnd) {
      thisWeek.push(rec);
    } else {
      comingUp.push(rec);
    }
  }

  const buckets: RecommendationBucket[] = [];
  if (tonight.length > 0) {
    buckets.push({
      key: "tonight",
      label: BUCKET_LABEL.tonight,
      recommendations: tonight,
    });
  }
  if (thisWeek.length > 0) {
    buckets.push({
      key: "this_week",
      label: BUCKET_LABEL.this_week,
      recommendations: thisWeek,
    });
  }
  if (comingUp.length > 0) {
    buckets.push({
      key: "coming_up",
      label: BUCKET_LABEL.coming_up,
      recommendations: comingUp,
    });
  }
  return buckets;
}
