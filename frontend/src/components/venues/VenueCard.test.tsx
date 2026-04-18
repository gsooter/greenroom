/**
 * Tests for the VenueCard server component.
 *
 * Covers the image-vs-fallback branch (the blush plan's "tinted name-block"
 * when image_url is null), address rendering, tag clamping, and the
 * Link href.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import VenueCard from "@/components/venues/VenueCard";
import type { VenueSummary } from "@/types";

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function venue(overrides: Partial<VenueSummary> = {}): VenueSummary {
  return {
    id: "v-1",
    name: "Black Cat",
    slug: "black-cat",
    address: "1811 14th St NW",
    image_url: "https://img.test/black-cat.jpg",
    tags: ["standing-room", "indie", "historic", "dropped"],
    city: {
      id: "c-1",
      name: "Washington",
      slug: "washington-dc",
      state: "DC",
      region: "DMV",
    },
    ...overrides,
  };
}

describe("VenueCard", () => {
  it("links to the venue detail page by slug", () => {
    const { container } = render(<VenueCard venue={venue()} />);
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/venues/black-cat");
  });

  it("renders the image background when image_url is present", () => {
    const { container } = render(<VenueCard venue={venue()} />);
    const images = container.querySelectorAll('[role="presentation"]');
    expect(images).toHaveLength(1);
    expect((images[0] as HTMLElement).style.backgroundImage).toContain(
      "https://img.test/black-cat.jpg",
    );
  });

  it("falls back to a tinted name-block when image_url is null", () => {
    const { container } = render(
      <VenueCard venue={venue({ image_url: null })} />,
    );
    const fallback = container.querySelector('[role="presentation"]');
    expect(fallback).not.toBeNull();
    expect(fallback?.className).toContain("bg-green-dark");
    // The venue name appears both inside the fallback block and as the
    // card heading — assert the fallback variant is rendered.
    expect(fallback?.textContent).toContain("Black Cat");
  });

  it("shows the address when present and hides it when null", () => {
    const { rerender } = render(<VenueCard venue={venue()} />);
    expect(screen.getByText("1811 14th St NW")).toBeInTheDocument();

    rerender(<VenueCard venue={venue({ address: null })} />);
    expect(screen.queryByText("1811 14th St NW")).not.toBeInTheDocument();
  });

  it("caps rendered tags at three", () => {
    render(<VenueCard venue={venue()} />);
    expect(screen.getByText("standing-room")).toBeInTheDocument();
    expect(screen.getByText("indie")).toBeInTheDocument();
    expect(screen.getByText("historic")).toBeInTheDocument();
    expect(screen.queryByText("dropped")).not.toBeInTheDocument();
  });

  it("includes the city + state in the region badge", () => {
    render(<VenueCard venue={venue()} />);
    expect(screen.getByText("Washington, DC")).toBeInTheDocument();
  });

  it("omits the region badge entirely when city is null", () => {
    render(<VenueCard venue={venue({ city: null })} />);
    expect(screen.queryByText(/Washington/)).not.toBeInTheDocument();
  });
});
