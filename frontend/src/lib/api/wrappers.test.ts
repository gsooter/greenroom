/**
 * Thin-wrapper tests for the typed API modules.
 *
 * Each module is a small facade over fetchJson. Rather than mocking
 * fetchJson per-module, we stub global.fetch and run the real helper —
 * that way every assertion also exercises URL building, query encoding,
 * and envelope unwrapping in one step.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  completeSpotifyOAuth,
  startSpotifyOAuth,
} from "@/lib/api/auth";
import { getCityBySlug, listCities } from "@/lib/api/cities";
import { getEvent, listEvents } from "@/lib/api/events";
import { deleteMe, getMe, updateMe } from "@/lib/api/me";
import {
  getMyTopArtists,
  listRecommendations,
  refreshRecommendations,
} from "@/lib/api/recommendations";
import {
  listSavedEvents,
  saveEvent,
  unsaveEvent,
} from "@/lib/api/saved-events";
import { getVenueBySlug, listVenues } from "@/lib/api/venues";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(): { url: URL; init: RequestInit } {
  const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]!;
  return { url: new URL(String(call[0])), init: call[1] ?? {} };
}

describe("api/events", () => {
  it("listEvents passes filters through as query params", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { page: 1, per_page: 20, total: 0, has_next: false } }),
    );
    await listEvents({
      region: "DMV",
      cityId: "c-1",
      venueIds: ["v-1", "v-2"],
      dateFrom: "2026-04-01",
      genres: ["indie", "folk"],
      page: 2,
      perPage: 10,
    });
    const { url } = lastCall();
    expect(url.pathname).toBe("/api/v1/events");
    expect(url.searchParams.get("region")).toBe("DMV");
    expect(url.searchParams.get("city_id")).toBe("c-1");
    expect(url.searchParams.getAll("venue_id")).toEqual(["v-1", "v-2"]);
    expect(url.searchParams.get("date_from")).toBe("2026-04-01");
    expect(url.searchParams.getAll("genre")).toEqual(["indie", "folk"]);
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("per_page")).toBe("10");
  });

  it("getEvent unwraps the envelope and URL-encodes the id", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { id: "e-1", slug: "a/b" } }),
    );
    const out = await getEvent("a/b");
    expect(out).toEqual({ id: "e-1", slug: "a/b" });
    expect(lastCall().url.pathname).toBe("/api/v1/events/a%2Fb");
  });
});

describe("api/venues", () => {
  it("listVenues encodes filters", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { page: 1, per_page: 20, total: 0, has_next: false } }),
    );
    await listVenues({ region: "DMV", activeOnly: true, page: 1, perPage: 50 });
    const { url } = lastCall();
    expect(url.pathname).toBe("/api/v1/venues");
    expect(url.searchParams.get("region")).toBe("DMV");
    expect(url.searchParams.get("active_only")).toBe("true");
  });

  it("getVenueBySlug unwraps the envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { id: "v-1", slug: "black-cat" } }),
    );
    const out = await getVenueBySlug("black-cat");
    expect(out).toEqual({ id: "v-1", slug: "black-cat" });
    expect(lastCall().url.pathname).toBe("/api/v1/venues/black-cat");
  });
});

describe("api/cities", () => {
  it("listCities encodes region and unwraps", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [{ id: "c-1", slug: "washington-dc" }] }),
    );
    const out = await listCities({ region: "DMV" });
    expect(out).toEqual([{ id: "c-1", slug: "washington-dc" }]);
    expect(lastCall().url.searchParams.get("region")).toBe("DMV");
  });

  it("getCityBySlug unwraps the envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { id: "c-1", slug: "washington-dc" } }),
    );
    await getCityBySlug("washington-dc");
    expect(lastCall().url.pathname).toBe("/api/v1/cities/washington-dc");
  });
});

describe("api/me", () => {
  it("getMe passes the token and unwraps", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { id: "u-1", email: "a@b" } }),
    );
    const user = await getMe("tok");
    expect(user).toEqual({ id: "u-1", email: "a@b" });
    const { init } = lastCall();
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok",
    );
  });

  it("updateMe PATCHes with a JSON body", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { id: "u-1" } }));
    await updateMe("tok", { display_name: "x" });
    const { init } = lastCall();
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(JSON.stringify({ display_name: "x" }));
  });

  it("deleteMe issues a DELETE and returns undefined", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const out = await deleteMe("tok");
    expect(out).toBeUndefined();
    expect(lastCall().init.method).toBe("DELETE");
  });
});

describe("api/auth", () => {
  it("startSpotifyOAuth unwraps the envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { authorize_url: "https://acct/spot", state: "xyz" } }),
    );
    const out = await startSpotifyOAuth();
    expect(out.state).toBe("xyz");
  });

  it("completeSpotifyOAuth posts code + state and unwraps", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { token: "t", user: { id: "u-1" } } }),
    );
    const out = await completeSpotifyOAuth("code-1", "state-1");
    expect(out.token).toBe("t");
    const { init } = lastCall();
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ code: "code-1", state: "state-1" }));
  });
});

describe("api/recommendations", () => {
  it("listRecommendations paginates and requires a token", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { page: 1, per_page: 20, total: 0, has_next: false } }),
    );
    await listRecommendations("tok", { page: 3, perPage: 25 });
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/me/recommendations");
    expect(url.searchParams.get("page")).toBe("3");
    expect(url.searchParams.get("per_page")).toBe("25");
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok",
    );
  });

  it("refreshRecommendations POSTs and unwraps the count", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { generated: 9 } }));
    const out = await refreshRecommendations("tok");
    expect(out).toEqual({ generated: 9 });
    expect(lastCall().init.method).toBe("POST");
  });

  it("getMyTopArtists unwraps the envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { items: [], total: 0 } }),
    );
    const out = await getMyTopArtists("tok");
    expect(out).toEqual({ items: [], total: 0 });
    expect(lastCall().url.pathname).toBe("/api/v1/me/spotify/top-artists");
  });
});

describe("api/saved-events", () => {
  it("saveEvent POSTs to the per-event endpoint and unwraps", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { saved_at: "2026-04-18T00:00:00.000Z", event: { id: "e" } } }),
    );
    const out = await saveEvent("tok", "e-1");
    expect(out.event).toEqual({ id: "e" });
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/events/e-1/save");
    expect(init.method).toBe("POST");
  });

  it("unsaveEvent issues a DELETE and returns void", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await unsaveEvent("tok", "e-1");
    expect(lastCall().init.method).toBe("DELETE");
  });

  it("listSavedEvents uses default perPage of 20 and page 1", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { page: 1, per_page: 20, total: 0, has_next: false } }),
    );
    await listSavedEvents("tok");
    const { url } = lastCall();
    expect(url.searchParams.get("page")).toBe("1");
    expect(url.searchParams.get("per_page")).toBe("20");
  });
});
