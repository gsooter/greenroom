/**
 * Tests for AuthNav.
 *
 * AuthNav returns null while the auth context is hydrating (no flash),
 * returns null for anonymous visitors when Spotify login is disabled,
 * shows a "Sign in" link when the flag is on, and renders the
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

let mockConfig = { spotifyLoginEnabled: false };

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

vi.mock("@/lib/config", () => ({
  config: new Proxy(
    {},
    {
      get: (_t, key) => (mockConfig as Record<string, unknown>)[key as string],
    },
  ),
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
    mockConfig = { spotifyLoginEnabled: false };
  });

  it("renders nothing while auth is hydrating", () => {
    const { container } = render(<AuthNav />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for anon visitors when the login flag is off", () => {
    mockAuth = { ...mockAuth, isLoading: false };
    const { container } = render(<AuthNav />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a Sign-in link for anon visitors when the login flag is on", () => {
    mockAuth = { ...mockAuth, isLoading: false };
    mockConfig = { spotifyLoginEnabled: true };
    render(<AuthNav />);
    const link = screen.getByRole("link", { name: "Sign in" });
    expect(link.getAttribute("href")).toBe("/login");
  });

  it("renders the authenticated cluster with the user's display name", () => {
    mockAuth = {
      user: userFixture({ display_name: "Garrett" }),
      isAuthenticated: true,
      isLoading: false,
      token: "tok",
    };
    render(<AuthNav />);
    expect(screen.getByRole("link", { name: "For you" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Saved" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Garrett" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
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
