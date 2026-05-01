/**
 * Tests for MobileBottomNav.
 *
 * The bottom nav is iOS-native shaped: four tabs for authenticated
 * visitors ([Home][Events][Map][Me]) and three for anonymous visitors
 * ([Home][Events][Map]). The Me tab routes straight to the consolidated
 * /me dashboard — no popover, no inline menu — so each tab is a single
 * tap. The nav also hides itself entirely on the /welcome flow so the
 * onboarding sheet can own the screen.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MobileBottomNav from "@/components/layout/MobileBottomNav";
import type { User } from "@/types";

interface MockAuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  token: string | null;
}

let mockAuth: MockAuthState = {
  user: null,
  isAuthenticated: false,
  isLoading: false,
  token: null,
};

let mockPathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), back: vi.fn() }),
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

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ ...mockAuth, logout: vi.fn() }),
}));

function userFixture(): User {
  return {
    id: "u-1",
    email: "fan@example.com",
    display_name: "Fan",
    spotify_user_id: "spot-fan",
  } as unknown as User;
}

describe("MobileBottomNav", () => {
  beforeEach(() => {
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: false,
      token: null,
    };
    mockPathname = "/";
  });

  it("renders Home, Events, and Map for anonymous visitors (3 tabs)", () => {
    render(<MobileBottomNav />);
    expect(screen.getByRole("link", { name: /home/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /events/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /map/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^me$/i })).toBeNull();
  });

  it("renders Home, Events, Map, and Me for signed-in visitors (4 tabs)", () => {
    mockAuth = {
      user: userFixture(),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<MobileBottomNav />);
    expect(screen.getByRole("link", { name: /home/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /events/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /map/i })).toBeInTheDocument();
    const meLink = screen.getByRole("link", { name: /^me$/i });
    expect(meLink.getAttribute("href")).toBe("/me");
  });

  it("points the Map tab at /map", () => {
    render(<MobileBottomNav />);
    const map = screen.getByRole("link", { name: /map/i });
    expect(map.getAttribute("href")).toBe("/map");
  });

  it("renders no auth-specific tab while the auth state is still hydrating", () => {
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: true,
      token: null,
    };
    render(<MobileBottomNav />);
    expect(screen.queryByRole("link", { name: /^me$/i })).toBeNull();
  });

  it("hides itself entirely on the /welcome onboarding flow", () => {
    mockPathname = "/welcome";
    const { container } = render(<MobileBottomNav />);
    expect(container.querySelector("nav")).toBeNull();
  });

  it("marks the Map tab active on /map and on the /near-me redirect target", () => {
    mockPathname = "/map";
    render(<MobileBottomNav />);
    const map = screen.getByRole("link", { name: /map/i });
    expect(map.getAttribute("aria-current")).toBe("page");
  });

  it("marks the Map tab active when the URL is /map?view=near-me", () => {
    mockPathname = "/map";
    render(<MobileBottomNav />);
    const map = screen.getByRole("link", { name: /map/i });
    expect(map.getAttribute("aria-current")).toBe("page");
  });

  it("applies the bottom-variant glass class so the layered chrome reaches it", () => {
    const { container } = render(<MobileBottomNav />);
    const nav = container.querySelector("nav");
    expect(nav?.className).toContain("app-glass-nav");
    expect(nav?.className).toContain("app-glass-nav--bottom");
  });
});
