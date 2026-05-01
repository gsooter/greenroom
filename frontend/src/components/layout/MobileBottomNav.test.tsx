/**
 * Tests for MobileBottomNav.
 *
 * The trailing slots reflect auth state: anonymous visitors see a
 * fourth "Sign in" tab, signed-in visitors see "For you" plus a "Me"
 * tab that opens a popover with Saved, Settings, and Sign out. Home /
 * Events / Venues always render regardless of auth state.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MobileBottomNav from "@/components/layout/MobileBottomNav";
import type { User } from "@/types";

const mockReplace = vi.fn();
const mockLogout = vi.fn();

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

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ replace: mockReplace, push: vi.fn(), back: vi.fn() }),
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
  useAuth: () => ({ ...mockAuth, logout: mockLogout }),
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
    mockReplace.mockReset();
    mockLogout.mockReset();
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: false,
      token: null,
    };
  });

  it("always renders the Home, Events, and Venues links", () => {
    render(<MobileBottomNav />);
    expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Events" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Venues" })).toBeInTheDocument();
  });

  it("shows a Sign-in link to anonymous visitors", () => {
    render(<MobileBottomNav />);
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link.getAttribute("href")).toBe("/login");
    expect(screen.queryByRole("link", { name: "For you" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Account menu" })).toBeNull();
  });

  it("shows For you and a Me tab to signed-in visitors", () => {
    mockAuth = {
      user: userFixture(),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<MobileBottomNav />);
    const link = screen.getByRole("link", { name: "For you" });
    expect(link.getAttribute("href")).toBe("/for-you");
    expect(
      screen.getByRole("button", { name: "Account menu" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
  });

  it("opens the Me menu with Saved, Settings, and Sign out", () => {
    mockAuth = {
      user: userFixture(),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<MobileBottomNav />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByRole("menuitem", { name: "Saved" })).toHaveAttribute(
      "href",
      "/saved",
    );
    expect(screen.getByRole("menuitem", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(
      screen.getByRole("menuitem", { name: "Sign out" }),
    ).toBeInTheDocument();
  });

  it("signs the user out when the Sign out menu item is clicked", () => {
    mockAuth = {
      user: userFixture(),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<MobileBottomNav />);
    fireEvent.click(screen.getByRole("button", { name: "Account menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(mockLogout).toHaveBeenCalledOnce();
    expect(mockReplace).toHaveBeenCalledWith("/");
  });

  it("applies the bottom-variant glass class so the layered chrome reaches it", () => {
    // The same .app-glass-nav rule that styles TopNav, with
    // .app-glass-nav--bottom flipping the highlight, shadow, border,
    // and lensing gradient so light catches the top edge of a fixed-
    // bottom nav.
    const { container } = render(<MobileBottomNav />);
    const nav = container.querySelector("nav");
    expect(nav?.className).toContain("app-glass-nav");
    expect(nav?.className).toContain("app-glass-nav--bottom");
  });

  it("renders no auth-specific tab while the auth state is still hydrating", () => {
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: true,
      token: null,
    };
    render(<MobileBottomNav />);
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    expect(screen.queryByRole("link", { name: "For you" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Account menu" })).toBeNull();
  });
});
