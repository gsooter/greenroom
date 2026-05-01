/**
 * Tests for AuthNav.
 *
 * AuthNav returns null while the auth context is hydrating (no flash),
 * renders a "Sign in" link for anonymous visitors, and renders the
 * authenticated cluster with logout wired to router.replace("/").
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AuthNav from "@/components/layout/AuthNav";
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
  isLoading: true,
  token: null,
};

vi.mock("next/navigation", () => ({
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

function userFixture(overrides: Partial<User> = {}): User {
  return {
    id: "u-1",
    email: "fan@example.com",
    display_name: "Fan",
    spotify_user_id: "spot-fan",
    ...overrides,
  } as User;
}

describe("AuthNav", () => {
  beforeEach(() => {
    mockReplace.mockReset();
    mockLogout.mockReset();
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: true,
      token: null,
    };
  });

  it("renders nothing while auth is hydrating", () => {
    const { container } = render(<AuthNav />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a Sign-in link for anon visitors once hydration settles", () => {
    mockAuth = { ...mockAuth, isLoading: false };
    render(<AuthNav />);
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link.getAttribute("href")).toBe("/login");
  });

  it("renders the Me entry point pointing at /me and a Sign out button", () => {
    mockAuth = {
      user: userFixture({ display_name: "Garrett" }),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<AuthNav />);
    const meLink = screen.getByRole("link", { name: "Garrett" });
    expect(meLink.getAttribute("href")).toBe("/me");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    // The desktop authenticated cluster mirrors the new mobile nav by
    // collapsing For You and Saved into the /me dashboard.
    expect(screen.queryByRole("link", { name: "For you" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Saved" })).toBeNull();
  });

  it("falls back to the email when display_name is blank", () => {
    mockAuth = {
      user: userFixture({ display_name: "   " }),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<AuthNav />);
    expect(
      screen.getByRole("link", { name: "fan@example.com" }),
    ).toBeInTheDocument();
  });

  it("logs out and redirects home when Sign out is clicked", () => {
    mockAuth = {
      user: userFixture(),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<AuthNav />);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(mockLogout).toHaveBeenCalledTimes(1);
    expect(mockReplace).toHaveBeenCalledWith("/");
  });
});
