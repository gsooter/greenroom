/**
 * Tests for NearMeShell — permission gating, fetch, filters, and surprise.
 *
 * TonightMap itself is mocked out because it pokes MapKit JS. The
 * browser Geolocation API is stubbed via a controllable getCurrentPosition
 * so the suite can exercise granted, denied, and unsupported states.
 */

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NearMeShell from "@/components/map/NearMeShell";
import { getNearMeEvents } from "@/lib/api/maps";
import type { NearMeEnvelope, NearMeEvent } from "@/types";

vi.mock("@/lib/api/maps", () => ({
  getNearMeEvents: vi.fn(),
}));

vi.mock("@/components/map/TonightMap", () => ({
  __esModule: true,
  default: ({ events }: { events: NearMeEvent[] }) => (
    <div data-testid="near-me-map">
      <span data-testid="map-event-count">{events.length}</span>
    </div>
  ),
}));

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const getNearMeEventsMock = vi.mocked(getNearMeEvents);

function makeEvent(id: string, distanceKm: number): NearMeEvent {
  return {
    id,
    slug: `slug-${id}`,
    title: `Event ${id}`,
    starts_at: null,
    artists: [],
    genres: ["indie"],
    image_url: null,
    ticket_url: null,
    min_price: null,
    max_price: null,
    venue: {
      id: `v-${id}`,
      name: `Venue ${id}`,
      slug: `venue-${id}`,
      latitude: 38.9,
      longitude: -77,
    },
    distance_km: distanceKm,
  };
}

function envelope(events: NearMeEvent[]): NearMeEnvelope {
  return {
    data: events,
    meta: {
      count: events.length,
      center: { latitude: 38.9, longitude: -77 },
      radius_km: 10,
      window: "tonight",
      date_from: "2026-04-22",
      date_to: "2026-04-22",
    },
  };
}

interface GeolocationHandles {
  grant: (latitude: number, longitude: number) => void;
  deny: () => void;
  getCurrentPosition: ReturnType<typeof vi.fn>;
}

function installGeolocation(): GeolocationHandles {
  let successCb:
    | ((position: {
        coords: { latitude: number; longitude: number };
      }) => void)
    | null = null;
  let errorCb: ((error: GeolocationPositionError) => void) | null = null;
  const getCurrentPosition = vi.fn(
    (
      success: (position: {
        coords: { latitude: number; longitude: number };
      }) => void,
      error: (error: GeolocationPositionError) => void,
    ) => {
      successCb = success;
      errorCb = error;
    },
  );
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: { getCurrentPosition },
  });
  return {
    getCurrentPosition,
    grant: (latitude, longitude) => {
      successCb?.({ coords: { latitude, longitude } });
    },
    deny: () => {
      errorCb?.({
        code: 1,
        message: "denied",
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
      } as GeolocationPositionError);
    },
  };
}

function removeGeolocation(): void {
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: undefined,
  });
}

beforeEach(() => {
  getNearMeEventsMock.mockReset();
  pushMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("NearMeShell", () => {
  it("shows the permission prompt before the user opts in", () => {
    installGeolocation();
    render(<NearMeShell />);
    expect(
      screen.getByRole("button", { name: /use my location/i }),
    ).toBeInTheDocument();
    expect(getNearMeEventsMock).not.toHaveBeenCalled();
  });

  it("fetches and renders events after the user grants location", async () => {
    const geo = installGeolocation();
    getNearMeEventsMock.mockResolvedValueOnce(
      envelope([makeEvent("1", 0.5), makeEvent("2", 2.1)]),
    );

    render(<NearMeShell />);
    fireEvent.click(screen.getByRole("button", { name: /use my location/i }));
    await act(async () => {
      geo.grant(38.9, -77);
    });

    await waitFor(() => {
      expect(getNearMeEventsMock).toHaveBeenCalledTimes(1);
    });
    const args = getNearMeEventsMock.mock.calls[0]![0];
    expect(args.latitude).toBeCloseTo(38.9);
    expect(args.longitude).toBeCloseTo(-77);
    expect(args.window).toBe("tonight");
    expect(args.radiusKm).toBe(10);

    await waitFor(() => {
      expect(screen.getByTestId("map-event-count").textContent).toBe("2");
    });
  });

  it("re-fetches when radius or window changes", async () => {
    const geo = installGeolocation();
    getNearMeEventsMock.mockResolvedValue(envelope([makeEvent("1", 1)]));

    render(<NearMeShell />);
    fireEvent.click(screen.getByRole("button", { name: /use my location/i }));
    await act(async () => {
      geo.grant(38.9, -77);
    });
    await waitFor(() => {
      expect(getNearMeEventsMock).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^5 km$/i }));
    });
    await waitFor(() => {
      expect(getNearMeEventsMock).toHaveBeenCalledTimes(2);
    });
    expect(getNearMeEventsMock.mock.calls[1]![0].radiusKm).toBe(5);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /this week/i }));
    });
    await waitFor(() => {
      expect(getNearMeEventsMock).toHaveBeenCalledTimes(3);
    });
    expect(getNearMeEventsMock.mock.calls[2]![0].window).toBe("week");
  });

  it("toggles between map and list views", async () => {
    const geo = installGeolocation();
    getNearMeEventsMock.mockResolvedValue(envelope([makeEvent("1", 0.3)]));

    render(<NearMeShell />);
    fireEvent.click(screen.getByRole("button", { name: /use my location/i }));
    await act(async () => {
      geo.grant(38.9, -77);
    });
    await waitFor(() => {
      expect(screen.getByTestId("near-me-map")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^list$/i }));
    });
    expect(screen.queryByTestId("near-me-map")).not.toBeInTheDocument();
    expect(screen.getByText(/Event 1/)).toBeInTheDocument();
    expect(screen.getByText(/300 m/)).toBeInTheDocument();
  });

  it("routes to a random event when Surprise me is clicked", async () => {
    const geo = installGeolocation();
    getNearMeEventsMock.mockResolvedValue(
      envelope([makeEvent("alpha", 0.5), makeEvent("beta", 2)]),
    );

    render(<NearMeShell />);
    fireEvent.click(screen.getByRole("button", { name: /use my location/i }));
    await act(async () => {
      geo.grant(38.9, -77);
    });
    await waitFor(() => {
      expect(screen.getByTestId("map-event-count").textContent).toBe("2");
    });

    const randomSpy = vi.spyOn(Math, "random").mockReturnValue(0);
    fireEvent.click(screen.getByRole("button", { name: /surprise me/i }));
    expect(pushMock).toHaveBeenCalledWith("/events/slug-alpha");
    randomSpy.mockRestore();
  });

  it("renders a denial fallback with retry when permission is refused", async () => {
    const geo = installGeolocation();
    render(<NearMeShell />);
    fireEvent.click(screen.getByRole("button", { name: /use my location/i }));
    await act(async () => {
      geo.deny();
    });
    expect(
      screen.getByRole("heading", { name: /location permission denied/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /try again/i }),
    ).toBeInTheDocument();
    expect(getNearMeEventsMock).not.toHaveBeenCalled();
  });

  it("renders an unsupported fallback when the Geolocation API is missing", () => {
    removeGeolocation();
    render(<NearMeShell />);
    expect(
      screen.getByRole("heading", {
        name: /location isn't available in this browser/i,
      }),
    ).toBeInTheDocument();
  });
});
