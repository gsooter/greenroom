/**
 * Tests for MobileBottomNav.
 *
 * The trailing nav slot reflects auth state: anon visitors (including
 * the brief window before the auth context has hydrated) see "Sign in",
 * while signed-in visitors see "For you". Home / Events / Venues are
 * always present so the bar never shrinks below four columns.
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

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
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
  useAuth: () => mockAuth,
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
  });

  it("shows For you to signed-in visitors instead of Sign in", () => {
    mockAuth = {
      user: userFixture(),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<MobileBottomNav />);
    const link = screen.getByRole("link", { name: "For you" });
    expect(link.getAttribute("href")).toBe("/for-you");
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
  });

  it("defaults to Sign in while auth state is still hydrating", () => {
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: true,
      token: null,
    };
    render(<MobileBottomNav />);
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });
});
