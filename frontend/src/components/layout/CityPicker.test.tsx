/**
 * Tests for CityPicker.
 *
 * Covers: option list renders with "All DMV cities" plus each city,
 * the current selection is reflected in the select's value, and
 * changing the selection pushes the new URL while preserving the
 * other search params (but always dropping `page`).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CityPicker from "@/components/layout/CityPicker";
import type { City } from "@/types";

const mockPush = vi.fn();
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/events",
  useSearchParams: () => mockSearchParams,
}));

function cities(): City[] {
  return [
    {
      id: "c-1",
      name: "Washington",
      slug: "washington-dc",
      state: "DC",
      region: "DMV",
      timezone: "America/New_York",
      description: null,
      is_active: true,
    },
    {
      id: "c-2",
      name: "Baltimore",
      slug: "baltimore",
      state: "MD",
      region: "DMV",
      timezone: "America/New_York",
      description: null,
      is_active: true,
    },
  ];
}

describe("CityPicker", () => {
  beforeEach(() => {
    mockPush.mockReset();
    for (const key of Array.from(mockSearchParams.keys())) {
      mockSearchParams.delete(key);
    }
  });

  it("renders 'All DMV cities' and one option per city", () => {
    render(<CityPicker cities={cities()} />);
    expect(screen.getByText("All DMV cities")).toBeInTheDocument();
    expect(screen.getByText("Washington, DC")).toBeInTheDocument();
    expect(screen.getByText("Baltimore, MD")).toBeInTheDocument();
  });

  it("reflects the ?city search param in the select value", () => {
    mockSearchParams.set("city", "baltimore");
    render(<CityPicker cities={cities()} />);
    expect(
      (screen.getByRole("combobox") as HTMLSelectElement).value,
    ).toBe("baltimore");
  });

  it("pushes /events?city=<slug> on change", () => {
    render(<CityPicker cities={cities()} />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "washington-dc" },
    });
    expect(mockPush).toHaveBeenCalledWith("/events?city=washington-dc");
  });

  it("clears the city param when 'All DMV cities' is selected", () => {
    mockSearchParams.set("city", "baltimore");
    mockSearchParams.set("window", "weekend");
    render(<CityPicker cities={cities()} />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "" },
    });
    // window should be preserved, city dropped.
    expect(mockPush).toHaveBeenCalledWith("/events?window=weekend");
  });

  it("drops the page param on any change so users land on page 1", () => {
    mockSearchParams.set("page", "4");
    render(<CityPicker cities={cities()} />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "baltimore" },
    });
    expect(mockPush).toHaveBeenCalledWith("/events?city=baltimore");
  });
});
