/**
 * Tests for AuthProvider, useAuth, and useRequireAuth.
 *
 * Covers: localStorage rehydration on mount, /me fetch, 401 → refresh
 * rotation (success and failure paths), login/logout round-trips,
 * refreshUser/refreshSession round-trips, and useRequireAuth's
 * redirect when the visitor is anonymous.
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
const refreshSession = vi.fn<
  (refreshToken: string) => Promise<{
    token: string;
    token_expires_at: string | null;
    refresh_token: string | null;
    refresh_token_expires_at: string | null;
    user: User;
  }>
>();
const logoutApi = vi.fn<
  (token: string, refreshToken?: string | null) => Promise<void>
>();

vi.mock("@/lib/api/me", () => ({
  getMe: (...args: unknown[]) => (getMe as unknown as Mock)(...args),
}));

vi.mock("@/lib/api/auth-identity", () => ({
  refreshSession: (...args: unknown[]) =>
    (refreshSession as unknown as Mock)(...args),
  logout: (...args: unknown[]) => (logoutApi as unknown as Mock)(...args),
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

function sessionPayload(
  overrides: Partial<{
    token: string;
    refresh_token: string | null;
    user: User;
  }> = {},
) {
  return {
    token: "rotated-tok",
    token_expires_at: null,
    refresh_token: "rotated-refresh",
    refresh_token_expires_at: null,
    user: user(),
    ...overrides,
  };
}

describe("AuthProvider", () => {
  beforeEach(() => {
    getMe.mockReset();
    refreshSession.mockReset();
    logoutApi.mockReset();
    logoutApi.mockResolvedValue(undefined);
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
    window.localStorage.setItem("greenroom.refresh_token", "stored-refresh");
    getMe.mockResolvedValueOnce(user({ display_name: "Stored" }));

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.display_name).toBe("Stored");
    expect(getMe).toHaveBeenCalledWith("stored-tok");
  });

  it("rotates the session when /me returns 401 and retries with the new token", async () => {
    window.localStorage.setItem("greenroom.token", "stale");
    window.localStorage.setItem("greenroom.refresh_token", "refresh-1");
    getMe.mockRejectedValueOnce(
      new ApiRequestError(401, "UNAUTHORIZED", "expired"),
    );
    refreshSession.mockResolvedValueOnce(
      sessionPayload({
        token: "rotated-tok",
        refresh_token: "refresh-2",
        user: user({ display_name: "Rotated" }),
      }),
    );
    getMe.mockResolvedValueOnce(user({ display_name: "Rotated" }));

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));
    expect(refreshSession).toHaveBeenCalledWith("refresh-1");
    expect(result.current.user?.display_name).toBe("Rotated");
    expect(result.current.token).toBe("rotated-tok");
    expect(window.localStorage.getItem("greenroom.token")).toBe("rotated-tok");
    expect(window.localStorage.getItem("greenroom.refresh_token")).toBe(
      "refresh-2",
    );
  });

  it("clears both tokens when the refresh rotation also fails with 401", async () => {
    window.localStorage.setItem("greenroom.token", "stale");
    window.localStorage.setItem("greenroom.refresh_token", "refresh-dead");
    getMe.mockRejectedValueOnce(
      new ApiRequestError(401, "UNAUTHORIZED", "expired"),
    );
    refreshSession.mockRejectedValueOnce(
      new ApiRequestError(401, "INVALID_REFRESH", "reused"),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
    expect(window.localStorage.getItem("greenroom.token")).toBeNull();
    expect(window.localStorage.getItem("greenroom.refresh_token")).toBeNull();
  });

  it("clears the session when /me returns 401 and there is no refresh token", async () => {
    window.localStorage.setItem("greenroom.token", "stale");
    getMe.mockRejectedValueOnce(
      new ApiRequestError(401, "UNAUTHORIZED", "expired"),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.token).toBeNull();
    expect(refreshSession).not.toHaveBeenCalled();
  });

  it("leaves the token in place on non-auth errors (e.g. 500)", async () => {
    window.localStorage.setItem("greenroom.token", "valid");
    window.localStorage.setItem("greenroom.refresh_token", "refresh");
    getMe.mockRejectedValueOnce(new ApiRequestError(500, "HTTP_ERROR", "boom"));

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.token).toBe("valid");
    expect(result.current.user).toBeNull();
    expect(window.localStorage.getItem("greenroom.token")).toBe("valid");
    expect(refreshSession).not.toHaveBeenCalled();
  });

  it("login persists both tokens and fetches /me", async () => {
    getMe.mockResolvedValueOnce(user({ display_name: "After" }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("new-tok", "new-refresh");
    });

    expect(window.localStorage.getItem("greenroom.token")).toBe("new-tok");
    expect(window.localStorage.getItem("greenroom.refresh_token")).toBe(
      "new-refresh",
    );
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.display_name).toBe("After");
  });

  it("login without a refresh token clears any stale refresh token", async () => {
    window.localStorage.setItem("greenroom.refresh_token", "leftover");
    getMe.mockResolvedValueOnce(user());

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login("new-tok");
    });

    expect(window.localStorage.getItem("greenroom.refresh_token")).toBeNull();
  });

  it("logout clears both tokens and revokes the refresh token server-side", async () => {
    window.localStorage.setItem("greenroom.token", "stored");
    window.localStorage.setItem("greenroom.refresh_token", "refresh");
    getMe.mockResolvedValueOnce(user());

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    act(() => {
      result.current.logout();
    });

    expect(result.current.user).toBeNull();
    expect(result.current.token).toBeNull();
    expect(window.localStorage.getItem("greenroom.token")).toBeNull();
    expect(window.localStorage.getItem("greenroom.refresh_token")).toBeNull();
    await waitFor(() =>
      expect(logoutApi).toHaveBeenCalledWith("stored", "refresh"),
    );
  });

  it("logout skips the API call when there is no token", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => {
      result.current.logout();
    });

    expect(logoutApi).not.toHaveBeenCalled();
  });

  it("logout swallows API failures (always succeeds locally)", async () => {
    window.localStorage.setItem("greenroom.token", "stored");
    window.localStorage.setItem("greenroom.refresh_token", "refresh");
    getMe.mockResolvedValueOnce(user());
    logoutApi.mockRejectedValueOnce(
      new ApiRequestError(500, "HTTP_ERROR", "boom"),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    act(() => {
      result.current.logout();
    });

    expect(result.current.token).toBeNull();
  });

  it("refreshUser re-fetches /me when a token is present and no-ops when not", async () => {
    window.localStorage.setItem("greenroom.token", "tok");
    getMe.mockResolvedValueOnce(user({ display_name: "First" }));

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    getMe.mockResolvedValueOnce(user({ display_name: "Updated" }));
    await act(async () => {
      await result.current.refreshUser();
    });
    expect(result.current.user?.display_name).toBe("Updated");

    act(() => {
      result.current.logout();
    });

    getMe.mockClear();
    await act(async () => {
      await result.current.refreshUser();
    });
    expect(getMe).not.toHaveBeenCalled();
  });

  it("refreshSession rotates tokens on demand and returns the new access token", async () => {
    window.localStorage.setItem("greenroom.token", "old-tok");
    window.localStorage.setItem("greenroom.refresh_token", "old-refresh");
    getMe.mockResolvedValueOnce(user());
    refreshSession.mockResolvedValueOnce(
      sessionPayload({ token: "next-tok", refresh_token: "next-refresh" }),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    let returned: string | null = null;
    await act(async () => {
      returned = await result.current.refreshSession();
    });
    expect(returned).toBe("next-tok");
    expect(result.current.token).toBe("next-tok");
    expect(window.localStorage.getItem("greenroom.refresh_token")).toBe(
      "next-refresh",
    );
  });

  it("refreshSession returns null when there is no refresh token stored", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let returned: string | null = "before";
    await act(async () => {
      returned = await result.current.refreshSession();
    });
    expect(returned).toBeNull();
    expect(refreshSession).not.toHaveBeenCalled();
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
    refreshSession.mockReset();
    logoutApi.mockReset();
    logoutApi.mockResolvedValue(undefined);
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
