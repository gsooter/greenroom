/**
 * Tests for TopNav.
 *
 * TopNav is an async server component that fetches cities at render
 * time. The interesting bits to assert here are the glass-chrome
 * contract (so the layered glass styling in globals.css is reachable)
 * and graceful degradation when the city list endpoint fails — the nav
 * itself must still render.
 */

import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TopNav from "@/components/layout/TopNav";
import { listCities } from "@/lib/api/cities";
import type { City } from "@/types";

vi.mock("@/lib/api/cities", () => ({
  listCities: vi.fn(),
}));

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

vi.mock("@/components/layout/AuthNav", () => ({
  __esModule: true,
  default: () => <div data-testid="auth-nav" />,
}));

vi.mock("@/components/layout/CityPicker", () => ({
  __esModule: true,
  default: ({ cities }: { cities: City[] }) => (
    <div data-testid="city-picker" data-count={cities.length} />
  ),
}));

const mockedListCities = vi.mocked(listCities);

function cityFixture(slug: string, name: string): City {
  return {
    id: slug,
    name,
    slug,
    state: "DC",
    region: "DMV",
  } as City;
}

describe("TopNav", () => {
  beforeEach(() => {
    mockedListCities.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("applies the .app-glass-nav class so the layered glass rule reaches it", async () => {
    // The 24px backdrop-blur, 1.8 saturation, 72% bg-base tint, border,
    // shadow, lensing gradient, and text-shadow halo all live in
    // globals.css under .app-glass-nav. Asserting the class is present
    // is the in-jsdom proxy for asserting the rule applies in the
    // browser, since jsdom doesn't load the global stylesheet.
    mockedListCities.mockResolvedValue([cityFixture("dc", "Washington")]);
    const ui = await TopNav();
    const { container } = render(ui);
    const header = container.querySelector("header");
    expect(header?.className).toContain("app-glass-nav");
  });

  it("still renders the nav when the city endpoint fails", async () => {
    // Backend hiccups must not break the chrome — the picker just
    // disappears for that render.
    mockedListCities.mockRejectedValue(new Error("nope"));
    const ui = await TopNav();
    const { container, queryByTestId } = render(ui);
    expect(container.querySelector("header")).not.toBeNull();
    expect(queryByTestId("city-picker")).toBeNull();
  });

  it("renders a CityPicker when listCities returns at least one city", async () => {
    mockedListCities.mockResolvedValue([
      cityFixture("dc", "Washington"),
      cityFixture("rva", "Richmond"),
    ]);
    const ui = await TopNav();
    const { getByTestId } = render(ui);
    const picker = getByTestId("city-picker");
    expect(picker.dataset.count).toBe("2");
  });

  it("renders the four core nav links", async () => {
    mockedListCities.mockResolvedValue([]);
    const ui = await TopNav();
    const { getByText } = render(ui);
    expect(getByText("Events")).toBeInTheDocument();
    expect(getByText("Tonight")).toBeInTheDocument();
    expect(getByText("Near Me")).toBeInTheDocument();
    expect(getByText("Venues")).toBeInTheDocument();
  });
});
