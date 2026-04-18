/**
 * Tests for AuthProvider, useAuth, and useRequireAuth.
 *
 * Covers: localStorage rehydration on mount, /me fetch, 401 → token
 * clear, login/logout/refresh round-trips, and useRequireAuth's redirect
 * when the visitor is anonymous.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import { ApiRequestError } from "@/lib/api/client";
import {
  AuthProvider,
  useAuth,
  useRequireAuth,
} from "@/lib/auth";
import type { User } from "@/types";

const getMe = vi.fn<(token: string) => Promise<User>>();

vi.mock("@/lib/api/me", () => ({
  getMe: (...args: unknown[]) => (getMe as unknown as Mock)(...args),
}));

const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn(), back: vi.fn() }),
}));

const wrapper = ({ children }: { children: React.ReactNode }): JSX.Element => (
  <AuthProvider>{children}</AuthProvider>
);

function user(overrides: Partial<User> = {}): User {
  return {
    id: "u-1",
    email: "fan@example.com",
    display_name: "Fan",
    spotify_user_id: "spot",
    ...overrides,
  } as User;
}

describe("AuthProvider", () => {
  beforeEach(() => {
    getMe.mockReset();
    mockReplace.mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("starts logged out and becomes not-loading when no token is stored", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.user).toBeNull();
    expect(result.current.isAuthenticated).toBe(false);
    expect(getMe).not.toHaveBeenCalled();
  });

  it("rehydrates the user from a stored token on mount", async () => {
    window.localStorage.setItem("greenroom.token", "stored-tok");
    getMe.mockResolvedValueOnce(user({ display_name: "Stored" }));

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.display_name).toBe("Stored");
    expect(getMe).toHaveBeenCalledWith("stored-tok");
  });

  it("clears the stored token when /me returns 401", async () => {
    window.localStorage.setItem("greenroom.token", "stale");
    getMe.mockRejectedValueOnce(
      new ApiRequestError(401, "UNAUTHORIZED", "expired"),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
    expect(window.localStorage.getItem("greenroom.token")).toBeNull();
  });

  it("leaves the token in place on non-auth errors (e.g. 500)", async () => {
    window.localStorage.setItem("greenroom.token", "valid");
    getMe.mockRejectedValueOnce(
      new ApiRequestError(500, "HTTP_ERROR", "boom"),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.token).toBe("valid");
    expect(result.current.user).toBeNull();
    expect(window.localStorage.getItem("greenroom.token")).toBe("valid");
  });

  it("login persists the token and fetches /me", async () => {
    getMe.mockResolvedValueOnce(user({ display_name: "After" }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("new-tok");
    });

    expect(window.localStorage.getItem("greenroom.token")).toBe("new-tok");
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.display_name).toBe("After");
  });

  it("logout clears token, user, and localStorage", async () => {
    window.localStorage.setItem("greenroom.token", "stored");
    getMe.mockResolvedValueOnce(user());

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    act(() => {
      result.current.logout();
    });

    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
    expect(window.localStorage.getItem("greenroom.token")).toBeNull();
  });

  it("refresh re-fetches /me when a token is present and no-ops when not", async () => {
    window.localStorage.setItem("greenroom.token", "tok");
    getMe.mockResolvedValueOnce(user({ display_name: "First" }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    getMe.mockResolvedValueOnce(user({ display_name: "Updated" }));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.user?.display_name).toBe("Updated");

    act(() => {
      result.current.logout();
    });

    getMe.mockClear();
    await act(async () => {
      await result.current.refresh();
    });
    expect(getMe).not.toHaveBeenCalled();
  });
});

describe("useAuth guard", () => {
  it("throws outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAuth())).toThrow(/useAuth must be called/);
    spy.mockRestore();
  });
});

describe("useRequireAuth", () => {
  beforeEach(() => {
    getMe.mockReset();
    mockReplace.mockReset();
    window.localStorage.clear();
  });

  it("redirects anonymous users to /login once hydration finishes", async () => {
    renderHook(() => useRequireAuth(), { wrapper });

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/login"));
  });

  it("does not redirect an authenticated user", async () => {
    window.localStorage.setItem("greenroom.token", "tok");
    getMe.mockResolvedValueOnce(user());

    const { result } = renderHook(() => useRequireAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    expect(mockReplace).not.toHaveBeenCalled();
  });
});
