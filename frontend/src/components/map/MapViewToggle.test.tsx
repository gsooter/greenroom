/**
 * Tests for MapViewToggle.
 *
 * The toggle is the user-facing surface for switching between the two
 * /map sub-views (Tonight vs Near Me). The new mobile bottom nav routes
 * both into the same /map tab, so the toggle must update the URL in a
 * way that's deep-linkable and that drops the param entirely on the
 * default view to keep canonical URLs clean.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MapViewToggle from "@/components/map/MapViewToggle";

const mockReplace = vi.fn();
let mockSearch = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(mockSearch),
}));

describe("MapViewToggle", () => {
  beforeEach(() => {
    mockReplace.mockReset();
    mockSearch = "";
  });

  it("renders both tabs and marks the active one", () => {
    render(<MapViewToggle active="tonight" />);
    const tonight = screen.getByRole("tab", { name: "Tonight" });
    const nearMe = screen.getByRole("tab", { name: "Near Me" });
    expect(tonight.getAttribute("aria-selected")).toBe("true");
    expect(nearMe.getAttribute("aria-selected")).toBe("false");
  });

  it("drops the view param when selecting Tonight (the default view)", () => {
    mockSearch = "view=near-me";
    render(<MapViewToggle active="near-me" />);
    fireEvent.click(screen.getByRole("tab", { name: "Tonight" }));
    expect(mockReplace).toHaveBeenCalledWith("/map");
  });

  it("sets view=near-me when selecting Near Me", () => {
    render(<MapViewToggle active="tonight" />);
    fireEvent.click(screen.getByRole("tab", { name: "Near Me" }));
    expect(mockReplace).toHaveBeenCalledWith("/map?view=near-me");
  });

  it("preserves any other query params already in the URL", () => {
    mockSearch = "ref=email";
    render(<MapViewToggle active="tonight" />);
    fireEvent.click(screen.getByRole("tab", { name: "Near Me" }));
    expect(mockReplace).toHaveBeenCalledWith("/map?ref=email&view=near-me");
  });
});
