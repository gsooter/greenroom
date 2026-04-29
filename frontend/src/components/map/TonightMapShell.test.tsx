/**
 * Tests for TonightMapShell — bucket count derivation and refetch
 * behavior when the active bucket changes.
 *
 * TonightMap itself is mocked out because it pokes MapKit JS; this
 * suite focuses on the shell's glue responsibilities.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TonightMapShell from "@/components/map/TonightMapShell";
import { getTonightMap } from "@/lib/api/maps";
import type { TonightMapEvent } from "@/types";

vi.mock("@/lib/api/maps", () => ({
  getTonightMap: vi.fn(),
}));

vi.mock("@/components/map/TonightMap", () => ({
  __esModule: true,
  default: ({ events, activeBucket }: { events: TonightMapEvent[]; activeBucket: string | null }) => (
    <div data-testid="tonight-map">
      <span data-testid="event-count">{events.length}</span>
      <span data-testid="active-bucket">{activeBucket ?? "all"}</span>
    </div>
  ),
}));

const getTonightMapMock = vi.mocked(getTonightMap);

function makeEvent(
  id: string,
  genres: string[],
  venueId: string = "v-1",
): TonightMapEvent {
  return {
    id,
    slug: `slug-${id}`,
    title: `Event ${id}`,
    starts_at: null,
    artists: [],
    genres,
    image_url: null,
    ticket_url: null,
    min_price: null,
    max_price: null,
    venue: {
      id: venueId,
      name: "Venue",
      slug: "venue",
      latitude: 38.9,
      longitude: -77,
    },
  };
}

beforeEach(() => {
  getTonightMapMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TonightMapShell", () => {
  it("counts initial events per bucket and totals correctly", () => {
    render(
      <TonightMapShell
        initialEvents={[
          makeEvent("1", ["indie"]),
          makeEvent("2", ["electronic"]),
          makeEvent("3", ["rock"]),
        ]}
        recommendations={[]}
      />,
    );
    expect(
      screen.getByRole("radio", { name: /^all3$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /indie \/ rock2/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /electronic1/i }),
    ).toBeInTheDocument();
  });

  it("re-fetches with the bucket's genre list when a pill is clicked", async () => {
    getTonightMapMock.mockResolvedValueOnce({
      data: [makeEvent("99", ["electronic"])],
      meta: { count: 1, date: "2026-04-21" },
    });

    render(
      <TonightMapShell
        initialEvents={[makeEvent("1", ["indie"])]}
        recommendations={[]}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: /electronic/i }));
    });

    await waitFor(() => {
      expect(getTonightMapMock).toHaveBeenCalledTimes(1);
    });
    const args = getTonightMapMock.mock.calls[0]![0];
    expect(args?.genres).toContain("electronic");
    await waitFor(() => {
      expect(screen.getByTestId("event-count").textContent).toBe("1");
      expect(screen.getByTestId("active-bucket").textContent).toBe("amber");
    });
  });

  it("does not fetch when the user returns to the All bucket", async () => {
    getTonightMapMock.mockResolvedValue({
      data: [],
      meta: { count: 0, date: "2026-04-21" },
    });

    render(
      <TonightMapShell
        initialEvents={[makeEvent("1", ["indie"])]}
        recommendations={[]}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: /indie/i }));
    });
    await waitFor(() => {
      expect(getTonightMapMock).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("radio", { name: /^all/i }));
    });
    // No additional fetch — All reverts to the server-fetched list.
    expect(getTonightMapMock).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId("event-count").textContent).toBe("1");
    });
  });
});
