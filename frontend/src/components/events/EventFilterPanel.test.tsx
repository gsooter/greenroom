/**
 * Tests for EventFilterPanel.
 *
 * The panel is a client component owning local form state and the
 * trigger button. The most important guarantees are:
 *
 *  - opens/closes correctly and reseeds from initialFilters on prop change
 *  - active-count badge reflects ``initialFilters``, not the in-flight
 *    draft (so the badge stays stable while the user is editing)
 *  - Apply pushes the URL with the new filter state and drops ``page``
 *  - Clear pushes the URL with no filter params
 *  - the free-only checkbox disables and clears the price input
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EventFilterPanel from "@/components/events/EventFilterPanel";
import { EMPTY_FILTERS, type EventFilters } from "@/lib/event-filters";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
}));

const GENRES = [
  { slug: "indie", label: "Indie" },
  { slug: "folk", label: "Folk" },
];

const VENUES = [
  { id: "v-1", name: "9:30 Club" },
  { id: "v-2", name: "Black Cat" },
];

function renderPanel(
  overrides: Partial<{
    initialFilters: EventFilters;
    baseParams: URLSearchParams;
  }> = {},
) {
  return render(
    <EventFilterPanel
      initialFilters={overrides.initialFilters ?? EMPTY_FILTERS}
      baseParams={overrides.baseParams ?? new URLSearchParams()}
      genres={GENRES}
      venues={VENUES}
    />,
  );
}

describe("EventFilterPanel", () => {
  beforeEach(() => {
    mockPush.mockReset();
  });

  it("trigger button shows no badge when no filters are active", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: /filters/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/filters active/i)).not.toBeInTheDocument();
  });

  it("trigger badge counts initialFilters dimensions", () => {
    renderPanel({
      initialFilters: {
        ...EMPTY_FILTERS,
        genres: ["indie"],
        availableOnly: true,
      },
    });
    expect(screen.getByLabelText("2 filters active")).toBeInTheDocument();
  });

  it("opens the dialog on trigger click", () => {
    renderPanel();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes the dialog when the close X is clicked", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    fireEvent.click(screen.getByLabelText("Close filters"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("toggling a genre chip flips its aria-pressed state", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    const indie = screen.getByRole("button", { name: "Indie" });
    expect(indie.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(indie);
    expect(indie.getAttribute("aria-pressed")).toBe("true");
  });

  it("Apply pushes /events with the encoded filters and drops page", () => {
    renderPanel({
      baseParams: new URLSearchParams("city=washington-dc&page=4"),
    });
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    fireEvent.click(screen.getByRole("button", { name: "Indie" }));
    fireEvent.click(screen.getByLabelText("Hide sold-out & cancelled"));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(mockPush).toHaveBeenCalledTimes(1);
    const firstCall = mockPush.mock.calls[0];
    const url = firstCall ? (firstCall[0] as string) : "";
    expect(url.startsWith("/events?")).toBe(true);
    expect(url).toContain("city=washington-dc");
    expect(url).toContain("genre=indie");
    expect(url).toContain("available=1");
    expect(url).not.toContain("page=");
  });

  it("Clear all pushes the path with no filter params and closes the dialog", () => {
    renderPanel({
      initialFilters: {
        ...EMPTY_FILTERS,
        genres: ["indie"],
        artistSearch: "phoebe",
      },
      baseParams: new URLSearchParams("city=washington-dc"),
    });
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));

    expect(mockPush).toHaveBeenCalledTimes(1);
    const firstCall = mockPush.mock.calls[0];
    expect(firstCall?.[0]).toBe("/events?city=washington-dc");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("checking 'Free shows only' disables and zeroes the price input", () => {
    renderPanel({
      initialFilters: { ...EMPTY_FILTERS, priceMax: 50 },
    });
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    const priceInput = screen.getByPlaceholderText(
      "No limit",
    ) as HTMLInputElement;
    expect(priceInput.value).toBe("50");

    fireEvent.click(screen.getByLabelText("Free shows only"));
    expect(priceInput.disabled).toBe(true);
    expect(priceInput.value).toBe("");
  });

  it("reseeds the form from initialFilters when the prop changes", () => {
    const { rerender } = render(
      <EventFilterPanel
        initialFilters={EMPTY_FILTERS}
        baseParams={new URLSearchParams()}
        genres={GENRES}
        venues={VENUES}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));

    act(() => {
      rerender(
        <EventFilterPanel
          initialFilters={{ ...EMPTY_FILTERS, artistSearch: "boygenius" }}
          baseParams={new URLSearchParams()}
          genres={GENRES}
          venues={VENUES}
        />,
      );
    });

    const artistInput = screen.getByPlaceholderText(
      "Search artist name…",
    ) as HTMLInputElement;
    expect(artistInput.value).toBe("boygenius");
  });

  it("backdrop click closes the dialog", () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /filters/i }));
    const dialog = screen.getByRole("dialog");
    const backdrop = dialog.parentElement as HTMLElement;
    fireEvent.click(backdrop);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
