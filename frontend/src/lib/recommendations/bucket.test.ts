/**
 * Tests for the For-You bucketizer.
 *
 * All "today" assertions are anchored to America/New_York. Every test
 * passes an explicit `now` so the suite is timezone- and clock-stable.
 */

import { describe, expect, it } from "vitest";

import { bucketizeRecommendations } from "@/lib/recommendations/bucket";
import type { EventSummary, Recommendation } from "@/types";

function event(startsAt: string | null, id = crypto.randomUUID()): EventSummary {
  return {
    id,
    title: `Show ${id.slice(0, 4)}`,
    slug: `show-${id.slice(0, 4)}`,
    starts_at: startsAt,
    artists: [],
    genres: [],
    image_url: null,
    min_price: null,
    max_price: null,
    prices_refreshed_at: null,
    status: "confirmed",
    venue: null,
  };
}

function rec(startsAt: string | null, id = crypto.randomUUID()): Recommendation {
  return {
    id,
    score: 0.5,
    generated_at: null,
    is_dismissed: false,
    match_reasons: [],
    score_breakdown: {},
    event: event(startsAt, id),
  };
}

// 2026-04-25 12:00 ET (a Saturday). Used as the anchor for every test
// so seven-day windows and "today" land deterministically.
const NOW = new Date("2026-04-25T16:00:00Z");

describe("bucketizeRecommendations", () => {
  it("returns no buckets for an empty input", () => {
    expect(bucketizeRecommendations([], NOW)).toEqual([]);
  });

  it("places shows starting today in the Tonight bucket", () => {
    const tonight = rec("2026-04-25T23:00:00Z");
    const buckets = bucketizeRecommendations([tonight], NOW);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]!.key).toBe("tonight");
    expect(buckets[0]!.label).toBe("Tonight");
    expect(buckets[0]!.recommendations).toEqual([tonight]);
  });

  it("places shows within the next 7 days in the This week bucket", () => {
    const inThree = rec("2026-04-28T23:00:00Z");
    const inSix = rec("2026-05-01T23:00:00Z");
    const buckets = bucketizeRecommendations([inThree, inSix], NOW);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]!.key).toBe("this_week");
    expect(buckets[0]!.recommendations).toEqual([inThree, inSix]);
  });

  it("places shows beyond 7 days in the Coming up bucket", () => {
    const farOut = rec("2026-05-15T23:00:00Z");
    const buckets = bucketizeRecommendations([farOut], NOW);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]!.key).toBe("coming_up");
  });

  it("returns buckets in fixed order Tonight → This week → Coming up", () => {
    const farOut = rec("2026-05-15T23:00:00Z");
    const tonight = rec("2026-04-25T23:00:00Z");
    const thisWeek = rec("2026-04-28T23:00:00Z");
    // Pass them in non-canonical order to prove the bucketizer sorts.
    const buckets = bucketizeRecommendations(
      [farOut, thisWeek, tonight],
      NOW,
    );
    expect(buckets.map((b) => b.key)).toEqual([
      "tonight",
      "this_week",
      "coming_up",
    ]);
  });

  it("preserves input order (score-desc) within each bucket", () => {
    const a = rec("2026-04-26T23:00:00Z");
    const b = rec("2026-04-27T23:00:00Z");
    const c = rec("2026-04-28T23:00:00Z");
    const buckets = bucketizeRecommendations([a, b, c], NOW);
    expect(buckets[0]!.recommendations).toEqual([a, b, c]);
  });

  it("drops empty buckets so headers never render alone", () => {
    const onlyTonight = rec("2026-04-25T23:00:00Z");
    const buckets = bucketizeRecommendations([onlyTonight], NOW);
    expect(buckets.map((b) => b.key)).toEqual(["tonight"]);
  });

  it("treats events with no starts_at as Coming up", () => {
    const tba = rec(null);
    const buckets = bucketizeRecommendations([tba], NOW);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]!.key).toBe("coming_up");
    expect(buckets[0]!.recommendations).toEqual([tba]);
  });

  it("treats events with an unparseable starts_at as Coming up", () => {
    const garbage = rec("not-a-date");
    const buckets = bucketizeRecommendations([garbage], NOW);
    expect(buckets[0]!.key).toBe("coming_up");
  });

  it("drops events that have already started before today (ET)", () => {
    const yesterday = rec("2026-04-24T23:00:00Z");
    const today = rec("2026-04-25T23:00:00Z");
    const buckets = bucketizeRecommendations([yesterday, today], NOW);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]!.recommendations).toEqual([today]);
  });

  it("anchors the day boundary to America/New_York, not UTC", () => {
    // 2026-04-26T03:00:00Z is still 2026-04-25 in ET (11pm Saturday).
    // A naive UTC bucketizer would call this "this week" instead of
    // "tonight".
    const lateSaturday = rec("2026-04-26T03:00:00Z");
    const buckets = bucketizeRecommendations([lateSaturday], NOW);
    expect(buckets[0]!.key).toBe("tonight");
  });
});
