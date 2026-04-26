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
  completeAppleOAuth,
  completeGoogleOAuth,
  completePasskeyAuthentication,
  completePasskeyRegistration,
  logout,
  refreshSession,
  requestMagicLink,
  startAppleOAuth,
  startGoogleOAuth,
  startPasskeyAuthentication,
  startPasskeyRegistration,
  verifyMagicLink,
} from "@/lib/api/auth-identity";
import {
  completeSpotifyOAuth,
  completeTidalOAuth,
  connectAppleMusic,
  getAppleMusicDeveloperToken,
  startSpotifyOAuth,
  startTidalOAuth,
} from "@/lib/api/auth";
import { getCityBySlug, listCities } from "@/lib/api/cities";
import { getEvent, listEvents } from "@/lib/api/events";
import { deleteMe, getMe, getMyMusicConnections, updateMe } from "@/lib/api/me";
import {
  getNotificationPreferences,
  pauseAllEmails,
  resumeAllEmails,
  updateNotificationPreferences,
} from "@/lib/api/notification-preferences";
import {
  getMapKitToken,
  getMapRecommendations,
  getTonightMap,
} from "@/lib/api/maps";
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

type CapturedInit = Omit<RequestInit, "headers"> & {
  headers: Record<string, string>;
};

function lastCall(): { url: URL; init: CapturedInit } {
  const call = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]!;
  const init = (call[1] ?? {}) as RequestInit;
  return {
    url: new URL(String(call[0])),
    init: {
      ...init,
      headers: (init.headers ?? {}) as Record<string, string>,
    },
  };
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

  it("getMyMusicConnections hits the connections endpoint with the token", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { connections: [] } }),
    );
    const out = await getMyMusicConnections("tok");
    expect(out).toEqual({ connections: [] });
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/me/music-connections");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });
});

describe("api/notification-preferences", () => {
  it("getNotificationPreferences GETs with bearer token", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { weekly_digest: false, paused: false } }),
    );
    const out = await getNotificationPreferences("tok");
    expect(out).toEqual({ weekly_digest: false, paused: false });
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/me/notification-preferences");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("updateNotificationPreferences PATCHes with the payload as JSON body", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { weekly_digest: true } }),
    );
    await updateNotificationPreferences("tok", {
      weekly_digest: true,
      digest_hour: 18,
    });
    const { init } = lastCall();
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(
      JSON.stringify({ weekly_digest: true, digest_hour: 18 }),
    );
  });

  it("pauseAllEmails POSTs to the pause-all path", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { paused: true } }));
    await pauseAllEmails("tok");
    const { url, init } = lastCall();
    expect(url.pathname).toBe(
      "/api/v1/me/notification-preferences/pause-all",
    );
    expect(init.method).toBe("POST");
  });

  it("resumeAllEmails POSTs to the resume-all path", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { paused: false } }));
    await resumeAllEmails("tok");
    const { url, init } = lastCall();
    expect(url.pathname).toBe(
      "/api/v1/me/notification-preferences/resume-all",
    );
    expect(init.method).toBe("POST");
  });
});

describe("api/auth", () => {
  it("startSpotifyOAuth forwards the bearer token and unwraps the envelope", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { authorize_url: "https://acct/spot", state: "xyz" } }),
    );
    const out = await startSpotifyOAuth("tok");
    expect(out.state).toBe("xyz");
    expect(lastCall().init.headers.Authorization).toBe("Bearer tok");
  });

  it("completeSpotifyOAuth posts code + state with the bearer token", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { user: { id: "u-1" } } }));
    const out = await completeSpotifyOAuth("tok", "code-1", "state-1");
    expect(out.user.id).toBe("u-1");
    const { init } = lastCall();
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ code: "code-1", state: "state-1" }));
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("startTidalOAuth GETs the start endpoint with the bearer token", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { authorize_url: "https://acct/tidal", state: "t-xyz" } }),
    );
    const out = await startTidalOAuth("tok");
    expect(out.state).toBe("t-xyz");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/tidal/start");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("completeTidalOAuth posts code + state with the bearer token", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { user: { id: "u-2" } } }));
    const out = await completeTidalOAuth("tok", "c", "s");
    expect(out.user.id).toBe("u-2");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/tidal/complete");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ code: "c", state: "s" }));
  });

  it("getAppleMusicDeveloperToken GETs the mint endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { developer_token: "dev-jwt" } }),
    );
    const out = await getAppleMusicDeveloperToken("tok");
    expect(out.developer_token).toBe("dev-jwt");
    expect(lastCall().url.pathname).toBe(
      "/api/v1/auth/apple-music/developer-token",
    );
  });

  it("connectAppleMusic POSTs the MUT under the music_user_token key", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { user: { id: "u-3" } } }));
    const out = await connectAppleMusic("tok", "mut-123");
    expect(out.user.id).toBe("u-3");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/apple-music/connect");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ music_user_token: "mut-123" }));
    expect(init.headers.Authorization).toBe("Bearer tok");
  });
});

describe("api/auth-identity", () => {
  it("requestMagicLink POSTs the email", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { email_sent: true } }));
    const out = await requestMagicLink("user@example.com");
    expect(out.email_sent).toBe(true);
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/magic-link/request");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ email: "user@example.com" }));
  });

  it("verifyMagicLink POSTs the token and unwraps the session", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        data: {
          token: "jwt",
          token_expires_at: null,
          refresh_token: null,
          refresh_token_expires_at: null,
          user: { id: "u-1" },
        },
      }),
    );
    const out = await verifyMagicLink("raw-token");
    expect(out.token).toBe("jwt");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/magic-link/verify");
    expect(init.body).toBe(JSON.stringify({ token: "raw-token" }));
  });

  it("startGoogleOAuth GETs the start endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { authorize_url: "https://acct/g", state: "g-xyz" } }),
    );
    const out = await startGoogleOAuth();
    expect(out.authorize_url).toBe("https://acct/g");
    expect(lastCall().url.pathname).toBe("/api/v1/auth/google/start");
  });

  it("completeGoogleOAuth POSTs code + state", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        data: {
          token: "jwt",
          token_expires_at: null,
          refresh_token: null,
          refresh_token_expires_at: null,
          user: { id: "u-g" },
        },
      }),
    );
    const out = await completeGoogleOAuth("g-code", "g-state");
    expect(out.user.id).toBe("u-g");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/google/complete");
    expect(init.body).toBe(JSON.stringify({ code: "g-code", state: "g-state" }));
  });

  it("startAppleOAuth GETs the start endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { authorize_url: "https://appleid/a", state: "a-xyz" } }),
    );
    const out = await startAppleOAuth();
    expect(out.state).toBe("a-xyz");
    expect(lastCall().url.pathname).toBe("/api/v1/auth/apple/start");
  });

  it("completeAppleOAuth POSTs code, state, and null user when omitted", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        data: {
          token: "jwt",
          token_expires_at: null,
          refresh_token: null,
          refresh_token_expires_at: null,
          user: { id: "u-a" },
        },
      }),
    );
    await completeAppleOAuth("a-code", "a-state");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/apple/complete");
    expect(init.body).toBe(
      JSON.stringify({ code: "a-code", state: "a-state", user: null }),
    );
  });

  it("completeAppleOAuth forwards the first-sign-in user payload", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        data: {
          token: "jwt",
          token_expires_at: null,
          refresh_token: null,
          refresh_token_expires_at: null,
          user: { id: "u-a" },
        },
      }),
    );
    const userBlob = { name: { firstName: "Ada" } };
    await completeAppleOAuth("a-code", "a-state", userBlob);
    const { init } = lastCall();
    expect(init.body).toBe(
      JSON.stringify({ code: "a-code", state: "a-state", user: userBlob }),
    );
  });

  it("startPasskeyRegistration requires a token", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { options: { challenge: "c" }, state: "p-state" } }),
    );
    const out = await startPasskeyRegistration("tok");
    expect(out.state).toBe("p-state");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/passkey/register/start");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("completePasskeyRegistration POSTs credential + state + name", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { registered: true } }));
    const credential = { id: "cred-1" } as unknown as Parameters<
      typeof completePasskeyRegistration
    >[1];
    const out = await completePasskeyRegistration(
      "tok",
      credential,
      "p-state",
      "MacBook",
    );
    expect(out.registered).toBe(true);
    const { init } = lastCall();
    expect(init.body).toBe(
      JSON.stringify({ credential, state: "p-state", name: "MacBook" }),
    );
  });

  it("completePasskeyRegistration defaults name to null when omitted", async () => {
    fetchMock.mockResolvedValueOnce(json({ data: { registered: true } }));
    const credential = { id: "cred-2" } as unknown as Parameters<
      typeof completePasskeyRegistration
    >[1];
    await completePasskeyRegistration("tok", credential, "p-state");
    const { init } = lastCall();
    expect(init.body).toBe(
      JSON.stringify({ credential, state: "p-state", name: null }),
    );
  });

  it("startPasskeyAuthentication POSTs anonymously", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { options: { challenge: "c" }, state: "p-state" } }),
    );
    await startPasskeyAuthentication();
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/passkey/authenticate/start");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("completePasskeyAuthentication POSTs credential + state", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        data: {
          token: "jwt",
          token_expires_at: null,
          refresh_token: null,
          refresh_token_expires_at: null,
          user: { id: "u-p" },
        },
      }),
    );
    const credential = { id: "cred-3" } as unknown as Parameters<
      typeof completePasskeyAuthentication
    >[0];
    const out = await completePasskeyAuthentication(credential, "p-state");
    expect(out.user.id).toBe("u-p");
    const { init } = lastCall();
    expect(init.body).toBe(
      JSON.stringify({ credential, state: "p-state" }),
    );
  });

  it("refreshSession POSTs the refresh_token", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        data: {
          token: "jwt-2",
          token_expires_at: null,
          refresh_token: "new-refresh",
          refresh_token_expires_at: null,
          user: { id: "u-r" },
        },
      }),
    );
    const out = await refreshSession("old-refresh");
    expect(out.refresh_token).toBe("new-refresh");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/refresh");
    expect(init.body).toBe(JSON.stringify({ refresh_token: "old-refresh" }));
  });

  it("logout POSTs with the refresh token when supplied", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await logout("tok", "rt-1");
    const { url, init } = lastCall();
    expect(url.pathname).toBe("/api/v1/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.body).toBe(JSON.stringify({ refresh_token: "rt-1" }));
  });

  it("logout POSTs without a body when no refresh token is supplied", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await logout("tok");
    const { init } = lastCall();
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
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

describe("api/maps", () => {
  it("getTonightMap joins genres into a comma-separated query", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { count: 0, date: "2026-04-21" } }),
    );
    const out = await getTonightMap({ genres: ["indie", "folk"] });
    expect(out.meta.date).toBe("2026-04-21");
    const { url } = lastCall();
    expect(url.pathname).toBe("/api/v1/maps/tonight");
    expect(url.searchParams.get("genres")).toBe("indie,folk");
  });

  it("getTonightMap omits the genres param when the list is empty", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { count: 0, date: "2026-04-21" } }),
    );
    await getTonightMap({ genres: [] });
    const { url } = lastCall();
    expect(url.searchParams.has("genres")).toBe(false);
  });

  it("getMapRecommendations forwards bbox + filters and unwraps data", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [{ id: "r-1" }], meta: { count: 1 } }),
    );
    const out = await getMapRecommendations({
      swLat: 38.8,
      swLng: -77.1,
      neLat: 38.95,
      neLng: -76.9,
      category: "food_drink",
      sort: "top",
      limit: 50,
      sessionId: "guest-abc",
    });
    expect(out).toEqual([{ id: "r-1" }]);
    const { url } = lastCall();
    expect(url.pathname).toBe("/api/v1/maps/recommendations");
    expect(url.searchParams.get("sw_lat")).toBe("38.8");
    expect(url.searchParams.get("ne_lng")).toBe("-76.9");
    expect(url.searchParams.get("category")).toBe("food_drink");
    expect(url.searchParams.get("sort")).toBe("top");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("session_id")).toBe("guest-abc");
  });

  it("getMapRecommendations forwards the bearer token when provided", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: [], meta: { count: 0 } }),
    );
    await getMapRecommendations({
      swLat: 0,
      swLng: 0,
      neLat: 1,
      neLng: 1,
      token: "tok",
    });
    expect(lastCall().init.headers.Authorization).toBe("Bearer tok");
  });

  it("getMapKitToken unwraps the envelope and encodes origin", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ data: { token: "mk.tok", expires_at: 1_700_000_000 } }),
    );
    const out = await getMapKitToken({ origin: "https://greenroom.fm" });
    expect(out.token).toBe("mk.tok");
    expect(out.expires_at).toBe(1_700_000_000);
    const { url } = lastCall();
    expect(url.pathname).toBe("/api/v1/maps/token");
    expect(url.searchParams.get("origin")).toBe("https://greenroom.fm");
  });
});
