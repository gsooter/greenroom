/**
 * Tests for the URL ↔ EventFilters codec.
 *
 * The page uses these helpers in two directions:
 *  - parseEventFilters reads the search-param record Next gives the
 *    server component and turns it into the structured shape the panel
 *    consumes.
 *  - applyFiltersToParams pushes a new state back into URLSearchParams
 *    so the panel and chips can build hrefs.
 *
 * The round-trip is the most important guarantee — anything else is
 * a presentation detail.
 */

import { describe, expect, it } from "vitest";

import {
  EMPTY_FILTERS,
  FILTER_PARAM_KEYS,
  applyFiltersToParams,
  clearFilterDimension,
  countActiveFilters,
  isEmptyFilters,
  parseEventFilters,
  type EventFilters,
} from "@/lib/event-filters";

describe("parseEventFilters", () => {
  it("returns empty filters when nothing is set", () => {
    expect(parseEventFilters({})).toEqual(EMPTY_FILTERS);
  });

  it("decodes comma-separated genre and venue lists", () => {
    const filters = parseEventFilters({
      genre: "indie,folk,jazz",
      venue: "abc,def",
    });
    expect(filters.genres).toEqual(["indie", "folk", "jazz"]);
    expect(filters.venueIds).toEqual(["abc", "def"]);
  });

  it("trims whitespace and drops empties from CSV params", () => {
    const filters = parseEventFilters({ genre: " indie , , folk " });
    expect(filters.genres).toEqual(["indie", "folk"]);
  });

  it("parses artist as a trimmed non-empty string or null", () => {
    expect(parseEventFilters({ artist: "phoebe" }).artistSearch).toBe("phoebe");
    expect(parseEventFilters({ artist: "  " }).artistSearch).toBeNull();
    expect(parseEventFilters({}).artistSearch).toBeNull();
  });

  it("parses price_max as a non-negative number, dropping invalid values", () => {
    expect(parseEventFilters({ price_max: "45.5" }).priceMax).toBe(45.5);
    expect(parseEventFilters({ price_max: "0" }).priceMax).toBe(0);
    expect(parseEventFilters({ price_max: "-10" }).priceMax).toBeNull();
    expect(parseEventFilters({ price_max: "free" }).priceMax).toBeNull();
  });

  it("accepts truthy short-forms for the boolean flags", () => {
    expect(parseEventFilters({ free: "1" }).freeOnly).toBe(true);
    expect(parseEventFilters({ free: "true" }).freeOnly).toBe(true);
    expect(parseEventFilters({ free: "YES" }).freeOnly).toBe(true);
    expect(parseEventFilters({ free: "0" }).freeOnly).toBe(false);
    expect(parseEventFilters({ available: "on" }).availableOnly).toBe(true);
  });

  it("parses date_from / date_to only when YYYY-MM-DD shaped", () => {
    expect(parseEventFilters({ date_from: "2026-05-01" }).dateFrom).toBe(
      "2026-05-01",
    );
    expect(parseEventFilters({ date_from: "tomorrow" }).dateFrom).toBeNull();
  });

  it("works with a URLSearchParams instance directly", () => {
    const params = new URLSearchParams("genre=indie,folk&free=1");
    const filters = parseEventFilters(params);
    expect(filters.genres).toEqual(["indie", "folk"]);
    expect(filters.freeOnly).toBe(true);
  });

  it("collapses Next's string[] into the first value", () => {
    const filters = parseEventFilters({ artist: ["phoebe", "ignored"] });
    expect(filters.artistSearch).toBe("phoebe");
  });
});

describe("applyFiltersToParams", () => {
  it("writes nothing for empty filters and removes any pre-existing keys", () => {
    const params = new URLSearchParams("genre=stale&free=1&city=dc");
    applyFiltersToParams(params, EMPTY_FILTERS);
    expect(params.get("genre")).toBeNull();
    expect(params.get("free")).toBeNull();
    expect(params.get("city")).toBe("dc");
  });

  it("encodes all populated dimensions", () => {
    const filters: EventFilters = {
      genres: ["indie", "folk"],
      venueIds: ["v1", "v2"],
      artistSearch: "phoebe",
      priceMax: 50,
      freeOnly: false,
      availableOnly: true,
      dateFrom: "2026-05-01",
      dateTo: "2026-05-31",
    };
    const params = applyFiltersToParams(new URLSearchParams(), filters);
    expect(params.get("genre")).toBe("indie,folk");
    expect(params.get("venue")).toBe("v1,v2");
    expect(params.get("artist")).toBe("phoebe");
    expect(params.get("price_max")).toBe("50");
    expect(params.get("available")).toBe("1");
    expect(params.get("date_from")).toBe("2026-05-01");
    expect(params.get("date_to")).toBe("2026-05-31");
  });

  it("free-only beats price_max in encoding (matches backend precedence)", () => {
    const filters: EventFilters = {
      ...EMPTY_FILTERS,
      priceMax: 25,
      freeOnly: true,
    };
    const params = applyFiltersToParams(new URLSearchParams(), filters);
    expect(params.get("free")).toBe("1");
    expect(params.get("price_max")).toBeNull();
  });

  it("round-trips through parseEventFilters", () => {
    const filters: EventFilters = {
      genres: ["indie"],
      venueIds: ["abc-123"],
      artistSearch: "boygenius",
      priceMax: null,
      freeOnly: true,
      availableOnly: true,
      dateFrom: "2026-06-01",
      dateTo: null,
    };
    const params = applyFiltersToParams(new URLSearchParams(), filters);
    expect(parseEventFilters(params)).toEqual(filters);
  });

  it("FILTER_PARAM_KEYS lists every key applyFiltersToParams may write", () => {
    const filters: EventFilters = {
      genres: ["g"],
      venueIds: ["v"],
      artistSearch: "a",
      priceMax: 10,
      freeOnly: false,
      availableOnly: true,
      dateFrom: "2026-01-01",
      dateTo: "2026-12-31",
    };
    const params = applyFiltersToParams(new URLSearchParams(), filters);
    const keys = Array.from(params.keys());
    for (const key of keys) {
      expect(FILTER_PARAM_KEYS).toContain(key);
    }
  });
});

describe("countActiveFilters / isEmptyFilters", () => {
  it("returns 0 for empty filters", () => {
    expect(countActiveFilters(EMPTY_FILTERS)).toBe(0);
    expect(isEmptyFilters(EMPTY_FILTERS)).toBe(true);
  });

  it("counts each populated dimension once", () => {
    expect(
      countActiveFilters({
        ...EMPTY_FILTERS,
        genres: ["indie"],
        artistSearch: "phoebe",
        availableOnly: true,
      }),
    ).toBe(3);
  });

  it("treats price+free as a single dimension (they collapse into one chip)", () => {
    expect(
      countActiveFilters({ ...EMPTY_FILTERS, priceMax: 30, freeOnly: false }),
    ).toBe(1);
    expect(
      countActiveFilters({ ...EMPTY_FILTERS, priceMax: 30, freeOnly: true }),
    ).toBe(1);
  });

  it("treats date_from + date_to as a single range dimension", () => {
    expect(
      countActiveFilters({
        ...EMPTY_FILTERS,
        dateFrom: "2026-05-01",
        dateTo: "2026-05-31",
      }),
    ).toBe(1);
  });
});

describe("clearFilterDimension", () => {
  const seeded: EventFilters = {
    genres: ["indie"],
    venueIds: ["v"],
    artistSearch: "phoebe",
    priceMax: 30,
    freeOnly: true,
    availableOnly: true,
    dateFrom: "2026-05-01",
    dateTo: "2026-05-31",
  };

  it("clearing 'price' wipes both priceMax and freeOnly", () => {
    const out = clearFilterDimension(seeded, "price");
    expect(out.priceMax).toBeNull();
    expect(out.freeOnly).toBe(false);
    expect(out.genres).toEqual(["indie"]);
  });

  it("clearing 'dateRange' wipes both endpoints", () => {
    const out = clearFilterDimension(seeded, "dateRange");
    expect(out.dateFrom).toBeNull();
    expect(out.dateTo).toBeNull();
  });

  it("returns a new object — does not mutate the input", () => {
    clearFilterDimension(seeded, "genres");
    expect(seeded.genres).toEqual(["indie"]);
  });
});
