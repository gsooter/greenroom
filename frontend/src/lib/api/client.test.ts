/**
 * Tests for the typed fetch helper.
 *
 * We stub `global.fetch` rather than standing up MSW — every branch
 * we care about (URL shape, query encoding, headers, error mapping,
 * 204 handling) can be asserted from the single fetch call.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiNotFoundError,
  ApiRequestError,
  fetchJson,
} from "@/lib/api/client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("fetchJson", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds a URL against the configured API base", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [] }));
    await fetchJson("/api/v1/events");
    const url = String(fetchMock.mock.calls[0]![0]);
    expect(url).toBe("http://test.api/api/v1/events");
  });

  it("encodes query params and skips undefined / empty string values", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: [] }));
    await fetchJson("/api/v1/events", {
      query: {
        city_id: "abc",
        region: "DMV",
        page: 2,
        empty: "",
        missing: undefined,
        multi: ["a", "b"],
      },
    });
    const url = new URL(String(fetchMock.mock.calls[0]![0]));
    expect(url.searchParams.get("city_id")).toBe("abc");
    expect(url.searchParams.get("region")).toBe("DMV");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.has("empty")).toBe(false);
    expect(url.searchParams.has("missing")).toBe(false);
    expect(url.searchParams.getAll("multi")).toEqual(["a", "b"]);
  });

  it("attaches Authorization header when a token is passed", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: {} }));
    await fetchJson("/api/v1/me", { token: "tok" });
    const init = fetchMock.mock.calls[0]![1]!;
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer tok",
    );
    // Token requests must not be cached.
    expect(init.cache).toBe("no-store");
  });

  it("sets Content-Type and stringifies body when body is provided", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ data: {} }));
    await fetchJson("/api/v1/events", {
      method: "POST",
      body: { a: 1 },
    });
    const init = fetchMock.mock.calls[0]![1]!;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect(init.body).toBe('{"a":1}');
  });

  it("returns undefined on a 204 No Content response", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const out = await fetchJson("/api/v1/events/x/save", { method: "DELETE" });
    expect(out).toBeUndefined();
  });

  it("maps a 404 to ApiNotFoundError with parsed code+message", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        jsonResponse(
          { error: { code: "EVENT_NOT_FOUND", message: "gone" } },
          404,
        ),
      ),
    );
    await expect(fetchJson("/api/v1/events/x")).rejects.toBeInstanceOf(
      ApiNotFoundError,
    );
    try {
      await fetchJson("/api/v1/events/x");
      throw new Error("expected fetchJson to throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiNotFoundError);
      const typed = err as ApiNotFoundError;
      expect(typed.status).toBe(404);
      expect(typed.code).toBe("EVENT_NOT_FOUND");
      expect(typed.message).toBe("gone");
    }
  });

  it("maps a non-404 failure to ApiRequestError with HTTP default when body is unparseable", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("<html>500</html>", {
        status: 500,
        headers: { "Content-Type": "text/html" },
      }),
    );
    await expect(fetchJson("/api/v1/events")).rejects.toSatisfy(
      (err: unknown) =>
        err instanceof ApiRequestError &&
        !(err instanceof ApiNotFoundError) &&
        err.status === 500 &&
        err.code === "HTTP_ERROR",
    );
  });
});
