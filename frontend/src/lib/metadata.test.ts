/**
 * Tests for Next.js Metadata builders.
 */

import { describe, expect, it } from "vitest";

import {
  absolutePageUrl,
  buildEventDetailMetadata,
  buildEventsIndexMetadata,
  buildHomeMetadata,
  buildPageMetadata,
  buildVenueDetailMetadata,
  buildVenuesIndexMetadata,
} from "@/lib/metadata";
import type { EventDetail, VenueDetail } from "@/types";

function event(overrides: Partial<EventDetail> = {}): EventDetail {
  return {
    id: "e-1",
    title: "Fake Show",
    slug: "fake-show",
    starts_at: "2026-05-02T23:00:00.000Z",
    artists: ["Band"],
    image_url: "https://img.test/e.jpg",
    min_price: 25,
    max_price: 55,
    status: "confirmed",
    venue: {
      id: "v-1",
      name: "Black Cat",
      slug: "black-cat",
      city: {
        id: "c-1",
        name: "Washington",
        slug: "washington-dc",
        state: "DC",
        region: "DMV",
      },
    },
    venue_id: "v-1",
    description: null,
    event_type: "concert",
    ends_at: null,
    doors_at: null,
    genres: ["indie"],
    spotify_artist_ids: [],
    ticket_url: null,
    source_url: null,
    created_at: "2026-04-01T00:00:00.000Z",
    updated_at: "2026-04-01T00:00:00.000Z",
    ...overrides,
  };
}

function venue(overrides: Partial<VenueDetail> = {}): VenueDetail {
  return {
    id: "v-1",
    city_id: "c-1",
    city: {
      id: "c-1",
      name: "Washington",
      slug: "washington-dc",
      state: "DC",
      region: "DMV",
    },
    name: "Black Cat",
    slug: "black-cat",
    address: "1811 14th St NW",
    latitude: null,
    longitude: null,
    capacity: 700,
    website_url: null,
    description: "DC institution.",
    image_url: "https://img.test/v.jpg",
    tags: ["standing-room"],
    is_active: true,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
    upcoming_events: [],
    upcoming_event_count: 0,
    ...overrides,
  };
}

describe("absolutePageUrl + buildPageMetadata", () => {
  it("joins the base URL without duplicate slashes", () => {
    expect(absolutePageUrl("/events")).toBe("http://test.base/events");
    expect(absolutePageUrl("venues")).toBe("http://test.base/venues");
  });

  it("writes canonical and open graph fields", () => {
    const md = buildPageMetadata({
      title: "X",
      description: "Y",
      path: "/x",
      image: "https://img.test/x.jpg",
    });
    expect(md.alternates?.canonical).toBe("http://test.base/x");
    expect(md.openGraph?.url).toBe("http://test.base/x");
    expect(md.openGraph?.images).toEqual([{ url: "https://img.test/x.jpg" }]);
    expect((md.twitter as { card?: string } | undefined)?.card).toBe(
      "summary_large_image",
    );
  });

  it("downgrades twitter card to 'summary' when no image", () => {
    const md = buildPageMetadata({ title: "X", description: "Y", path: "/x" });
    expect((md.twitter as { card?: string } | undefined)?.card).toBe("summary");
    expect(md.openGraph?.images).toBeUndefined();
  });
});

describe("index-page metadata builders", () => {
  it("defaults home metadata cleanly", () => {
    const md = buildHomeMetadata();
    expect(md.title).toMatch(/Greenroom/);
    expect(md.alternates?.canonical).toBe("http://test.base/");
  });

  it("scopes events index by city name when available", () => {
    expect(String(buildEventsIndexMetadata("Washington").title)).toContain(
      "Washington",
    );
    expect(String(buildEventsIndexMetadata(null).title)).toContain("DMV");
  });

  it("scopes venues index by city name when available", () => {
    expect(String(buildVenuesIndexMetadata("Baltimore").title)).toContain(
      "Baltimore",
    );
    expect(String(buildVenuesIndexMetadata(null).title)).toContain("DMV");
  });
});

describe("buildEventDetailMetadata", () => {
  it("composes title and description with venue + date", () => {
    const md = buildEventDetailMetadata(event());
    expect(String(md.title)).toContain("Black Cat");
    expect(String(md.description)).toContain("Fake Show");
    expect(String(md.description)).toContain("Black Cat");
    // Price range is interpolated.
    expect(String(md.description)).toMatch(/\$/);
    expect(md.alternates?.canonical).toBe(
      "http://test.base/events/fake-show",
    );
  });

  it("degrades gracefully when venue and price are absent", () => {
    const md = buildEventDetailMetadata(
      event({ venue: null, min_price: null, max_price: null }),
    );
    expect(String(md.title)).toContain("TBA");
    expect(String(md.description)).toContain("Washington DC");
  });
});

describe("buildVenueDetailMetadata", () => {
  it("includes city + state in the title", () => {
    const md = buildVenueDetailMetadata(venue());
    expect(String(md.title)).toContain("Washington, DC");
    expect(String(md.description)).toContain("DC institution");
  });

  it("falls back to a generated description when description is null", () => {
    const md = buildVenueDetailMetadata(venue({ description: null }));
    expect(String(md.description)).toContain("Upcoming concerts");
  });

  it("degrades to 'Washington DC' when city is null", () => {
    const md = buildVenueDetailMetadata(venue({ city: null }));
    expect(String(md.title)).toContain("Washington DC");
  });
});
