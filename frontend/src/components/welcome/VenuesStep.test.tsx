/**
 * Tests for VenuesStep.
 *
 * The selection model is local-only until Continue, which batches
 * into a single bulk-follow write. These tests cover that round-trip
 * shape — what IDs actually go to the API, and what happens on skip
 * (no write at all).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VenuesStep } from "@/components/welcome/VenuesStep";
import type { VenueSummary } from "@/types";

const listVenues = vi.fn();
const followVenuesBulk = vi.fn();

vi.mock("@/lib/api/venues", () => ({
  listVenues: (params: unknown) => listVenues(params),
}));

vi.mock("@/lib/api/follows", () => ({
  followVenuesBulk: (token: string, ids: string[]) =>
    followVenuesBulk(token, ids),
}));

function venue(id: string, name: string): VenueSummary {
  return {
    id,
    name,
    slug: name.toLowerCase(),
    address: "123 U St NW",
    image_url: null,
    tags: [],
    city: null,
  };
}

describe("VenuesStep", () => {
  beforeEach(() => {
    listVenues.mockReset();
    followVenuesBulk.mockReset();
    listVenues.mockResolvedValue({
      data: [venue("v-1", "Black Cat"), venue("v-2", "9:30 Club")],
      meta: { total: 2, page: 1, per_page: 100, has_next: false },
    });
    followVenuesBulk.mockResolvedValue(2);
  });

  it("bulk-follows the selected venues then advances on Continue", async () => {
    const onDone = vi.fn();
    render(<VenuesStep token="jwt" onDone={onDone} onSkip={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: /Black Cat/i }));
    fireEvent.click(screen.getByRole("button", { name: /9:30 Club/i }));
    fireEvent.click(
      screen.getByRole("button", { name: /Follow 2 & continue/i }),
    );

    await waitFor(() =>
      expect(followVenuesBulk).toHaveBeenCalledWith("jwt", ["v-1", "v-2"]),
    );
    expect(onDone).toHaveBeenCalled();
  });

  it("advances without a bulk write if the user selected nothing", async () => {
    const onDone = vi.fn();
    render(<VenuesStep token="jwt" onDone={onDone} onSkip={vi.fn()} />);
    await screen.findByRole("button", { name: /Black Cat/i });

    fireEvent.click(screen.getByRole("button", { name: /^Continue$/ }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(followVenuesBulk).not.toHaveBeenCalled();
  });

  it("invokes onSkip without any write", async () => {
    const onSkip = vi.fn();
    render(<VenuesStep token="jwt" onDone={vi.fn()} onSkip={onSkip} />);
    await screen.findByRole("button", { name: /Black Cat/i });

    fireEvent.click(screen.getByRole("button", { name: /Skip for now/i }));
    expect(onSkip).toHaveBeenCalled();
    expect(followVenuesBulk).not.toHaveBeenCalled();
  });
});
