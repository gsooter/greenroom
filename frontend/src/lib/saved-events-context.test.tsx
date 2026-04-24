/**
 * Tests for SavedEventsProvider / useSavedEvents.
 *
 * Covers: anon sessions stay empty and no-op toggles; authed sessions
 * hydrate from the paginated API; optimistic save / unsave; rollback on
 * network failure; deduping when a concurrent toggle races; and the
 * useSavedEvents guard outside the provider.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import {
  SavedEventsProvider,
  useSavedEvents,
} from "@/lib/saved-events-context";
import type { EventSummary, Paginated, SavedEvent } from "@/types";

const listSavedEvents = vi.fn<
  (token: string, params?: { page?: number; perPage?: number }) => Promise<
    Paginated<SavedEvent>
  >
>();
const saveEvent =
  vi.fn<(token: string, eventId: string) => Promise<SavedEvent>>();
const unsaveEvent = vi.fn<(token: string, eventId: string) => Promise<void>>();

let mockAuth: {
  token: string | null;
  isAuthenticated: boolean;
} = { token: null, isAuthenticated: false };

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

vi.mock("@/lib/api/saved-events", () => ({
  listSavedEvents: (...args: unknown[]) =>
    (listSavedEvents as unknown as Mock)(...args),
  saveEvent: (...args: unknown[]) =>
    (saveEvent as unknown as Mock)(...args),
  unsaveEvent: (...args: unknown[]) =>
    (unsaveEvent as unknown as Mock)(...args),
}));

function eventSummary(id: string, title = `Show ${id}`): EventSummary {
  return {
    id,
    title,
    slug: `show-${id}`,
    starts_at: "2026-05-02T23:00:00.000Z",
    artists: ["Band"],
    genres: [],
    image_url: null,
    min_price: null,
    max_price: null,
    status: "confirmed",
    venue: null,
  };
}

function savedEntry(id: string): SavedEvent {
  return { saved_at: "2026-04-18T00:00:00.000Z", event: eventSummary(id) };
}

function page(
  items: SavedEvent[],
  pageNum = 1,
  hasNext = false,
): Paginated<SavedEvent> {
  return {
    data: items,
    meta: {
      page: pageNum,
      per_page: items.length,
      total: items.length,
      has_next: hasNext,
    },
  };
}

const wrapper = ({ children }: { children: React.ReactNode }): JSX.Element => (
  <SavedEventsProvider>{children}</SavedEventsProvider>
);

describe("SavedEventsProvider", () => {
  beforeEach(() => {
    listSavedEvents.mockReset();
    saveEvent.mockReset();
    unsaveEvent.mockReset();
    mockAuth = { token: null, isAuthenticated: false };
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("stays empty and doesn't call the API when the user is anonymous", async () => {
    const { result } = renderHook(() => useSavedEvents(), { wrapper });
    expect(result.current.isSaved("x")).toBe(false);
    await act(async () => {
      await result.current.toggle("x");
    });
    expect(listSavedEvents).not.toHaveBeenCalled();
    expect(saveEvent).not.toHaveBeenCalled();
    expect(unsaveEvent).not.toHaveBeenCalled();
  });

  it("hydrates the saved set when the user signs in", async () => {
    mockAuth = { token: "tok", isAuthenticated: true };
    listSavedEvents.mockResolvedValueOnce(page([savedEntry("a")]));

    const { result } = renderHook(() => useSavedEvents(), { wrapper });

    await waitFor(() => expect(result.current.isReady).toBe(true));
    expect(result.current.isSaved("a")).toBe(true);
    expect(result.current.isSaved("b")).toBe(false);
  });

  it("walks pagination until has_next is false", async () => {
    mockAuth = { token: "tok", isAuthenticated: true };
    listSavedEvents
      .mockResolvedValueOnce(page([savedEntry("a")], 1, true))
      .mockResolvedValueOnce(page([savedEntry("b")], 2, false));

    const { result } = renderHook(() => useSavedEvents(), { wrapper });

    await waitFor(() => expect(result.current.isReady).toBe(true));
    expect(result.current.savedEvents.map((e) => e.event.id).sort()).toEqual([
      "a",
      "b",
    ]);
    expect(listSavedEvents).toHaveBeenCalledTimes(2);
  });

  it("optimistically adds an unsaved event on toggle, then reconciles from the server", async () => {
    mockAuth = { token: "tok", isAuthenticated: true };
    listSavedEvents.mockResolvedValueOnce(page([]));
    const fresh = savedEntry("new");
    let resolveSave: (value: SavedEvent) => void = () => {};
    saveEvent.mockImplementation(
      () => new Promise<SavedEvent>((r) => (resolveSave = r)),
    );

    const { result } = renderHook(() => useSavedEvents(), { wrapper });
    await waitFor(() => expect(result.current.isReady).toBe(true));

    // Kick off the toggle; await only after we flush the server response.
    let toggling: Promise<void> = Promise.resolve();
    act(() => {
      toggling = result.current.toggle("new");
    });

    await act(async () => {
      resolveSave(fresh);
      await toggling;
    });

    expect(saveEvent).toHaveBeenCalledWith("tok", "new");
    expect(result.current.isSaved("new")).toBe(true);
  });

  it("optimistically removes a saved event on toggle", async () => {
    mockAuth = { token: "tok", isAuthenticated: true };
    listSavedEvents.mockResolvedValueOnce(page([savedEntry("a")]));
    unsaveEvent.mockResolvedValueOnce(undefined);

    const { result } = renderHook(() => useSavedEvents(), { wrapper });
    await waitFor(() => expect(result.current.isReady).toBe(true));

    await act(async () => {
      await result.current.toggle("a");
    });

    expect(unsaveEvent).toHaveBeenCalledWith("tok", "a");
    expect(result.current.isSaved("a")).toBe(false);
  });

  it("rolls back the optimistic state when the API call fails", async () => {
    mockAuth = { token: "tok", isAuthenticated: true };
    listSavedEvents.mockResolvedValueOnce(page([savedEntry("a")]));
    unsaveEvent.mockRejectedValueOnce(new Error("boom"));

    const { result } = renderHook(() => useSavedEvents(), { wrapper });
    await waitFor(() => expect(result.current.isReady).toBe(true));

    await act(async () => {
      await result.current.toggle("a");
    });

    // Rolled back to the pre-toggle snapshot.
    expect(result.current.isSaved("a")).toBe(true);
  });

  it("skips concurrent toggles for the same event id", async () => {
    mockAuth = { token: "tok", isAuthenticated: true };
    listSavedEvents.mockResolvedValueOnce(page([]));
    saveEvent.mockImplementation(
      () => new Promise(() => {}), // never resolves
    );

    const { result } = renderHook(() => useSavedEvents(), { wrapper });
    await waitFor(() => expect(result.current.isReady).toBe(true));

    act(() => {
      void result.current.toggle("x");
      void result.current.toggle("x");
    });

    expect(saveEvent).toHaveBeenCalledTimes(1);
  });
});

describe("useSavedEvents", () => {
  it("throws a clear error when used outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useSavedEvents())).toThrow(
      /useSavedEvents must be called/,
    );
    spy.mockRestore();
  });
});
