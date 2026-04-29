/**
 * Client-side auth context and hooks.
 *
 * The frontend stores the access + refresh tokens issued by Greenroom's
 * `/api/v1/auth/*` proxies (Knuckles-backed) in `localStorage`.
 * Reloading the app rehydrates the context and fetches `/me`; if the
 * access token has expired, we transparently rotate via the refresh
 * token before giving up on the session.
 *
 * Server components MUST NOT import this module — it is "use client"
 * end to end. Browse pages stay purely SSR and don't need auth state
 * to render.
 */

"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  logout as logoutApi,
  refreshSession as refreshSessionApi,
} from "@/lib/api/auth-identity";
import { ApiRequestError } from "@/lib/api/client";
import { getMe } from "@/lib/api/me";
import type { User } from "@/types";

const TOKEN_STORAGE_KEY = "greenroom.token";
const REFRESH_STORAGE_KEY = "greenroom.refresh_token";

interface SessionTokens {
  token: string;
  refreshToken: string | null;
}

interface AuthState {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (token: string, refreshToken?: string | null) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  refreshSession: () => Promise<string | null>;
}

const AuthContext = createContext<AuthState | null>(null);

function readStoredTokens(): SessionTokens | null {
  if (typeof window === "undefined") return null;
  try {
    const token = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (!token) return null;
    const refreshToken = window.localStorage.getItem(REFRESH_STORAGE_KEY);
    return { token, refreshToken };
  } catch {
    return null;
  }
}

function writeStoredTokens(tokens: SessionTokens | null): void {
  if (typeof window === "undefined") return;
  try {
    if (tokens === null) {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      window.localStorage.removeItem(REFRESH_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(TOKEN_STORAGE_KEY, tokens.token);
    if (tokens.refreshToken) {
      window.localStorage.setItem(REFRESH_STORAGE_KEY, tokens.refreshToken);
    } else {
      window.localStorage.removeItem(REFRESH_STORAGE_KEY);
    }
  } catch {
    /* storage may be disabled; fail quietly */
  }
}

function isAuthFailure(err: unknown): boolean {
  return (
    err instanceof ApiRequestError && (err.status === 401 || err.status === 403)
  );
}

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [token, setToken] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const refreshTokenRef = useRef<string | null>(null);
  const refreshInFlightRef = useRef<Promise<string | null> | null>(null);

  useEffect(() => {
    refreshTokenRef.current = refreshToken;
  }, [refreshToken]);

  const clearSession = useCallback((): void => {
    writeStoredTokens(null);
    setToken(null);
    setRefreshToken(null);
    setUser(null);
  }, []);

  const applySession = useCallback(
    (nextToken: string, nextRefreshToken: string | null): void => {
      writeStoredTokens({ token: nextToken, refreshToken: nextRefreshToken });
      setToken(nextToken);
      setRefreshToken(nextRefreshToken);
    },
    [],
  );

  const rotateSession = useCallback(async (): Promise<string | null> => {
    const current = refreshTokenRef.current;
    if (!current) return null;

    if (refreshInFlightRef.current) {
      return refreshInFlightRef.current;
    }

    const attempt = (async (): Promise<string | null> => {
      try {
        const session = await refreshSessionApi(current);
        applySession(session.token, session.refresh_token);
        return session.token;
      } catch (err) {
        if (isAuthFailure(err)) {
          clearSession();
        }
        return null;
      } finally {
        refreshInFlightRef.current = null;
      }
    })();
    refreshInFlightRef.current = attempt;
    return attempt;
  }, [applySession, clearSession]);

  const fetchUser = useCallback(
    async (activeToken: string): Promise<void> => {
      try {
        const fetched = await getMe(activeToken);
        setUser(fetched);
        return;
      } catch (err) {
        if (!isAuthFailure(err)) {
          setUser(null);
          return;
        }
      }

      const rotated = await rotateSession();
      if (!rotated) {
        clearSession();
        return;
      }
      try {
        setUser(await getMe(rotated));
      } catch (err) {
        if (isAuthFailure(err)) {
          clearSession();
        } else {
          setUser(null);
        }
      }
    },
    [rotateSession, clearSession],
  );

  useEffect(() => {
    const stored = readStoredTokens();
    if (!stored) {
      setIsLoading(false);
      return;
    }
    setToken(stored.token);
    setRefreshToken(stored.refreshToken);
    refreshTokenRef.current = stored.refreshToken;
    void fetchUser(stored.token).finally(() => setIsLoading(false));
  }, [fetchUser]);

  const login = useCallback(
    async (nextToken: string, nextRefreshToken: string | null = null): Promise<void> => {
      applySession(nextToken, nextRefreshToken);
      setIsLoading(true);
      await fetchUser(nextToken);
      setIsLoading(false);
    },
    [applySession, fetchUser],
  );

  const logout = useCallback((): void => {
    const currentToken = token;
    const currentRefreshToken = refreshTokenRef.current;
    clearSession();
    if (currentToken) {
      void logoutApi(currentToken, currentRefreshToken).catch(() => {
        /* Best-effort: logout always succeeds locally. */
      });
    }
  }, [token, clearSession]);

  const refreshUser = useCallback(async (): Promise<void> => {
    if (!token) return;
    await fetchUser(token);
  }, [token, fetchUser]);

  const value = useMemo<AuthState>(
    () => ({
      user,
      token,
      isLoading,
      isAuthenticated: Boolean(user && token),
      login,
      logout,
      refreshUser,
      refreshSession: rotateSession,
    }),
    [user, token, isLoading, login, logout, refreshUser, rotateSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be called inside <AuthProvider>.");
  }
  return ctx;
}

/**
 * Client hook: redirect the visitor to /login if they are not authenticated.
 * Call from the top of any page that requires a valid session.
 */
export function useRequireAuth(): AuthState {
  const auth = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!auth.isLoading && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.isAuthenticated, auth.isLoading, router]);

  return auth;
}

/**
 * Client hook: gate a page on both auth and completed onboarding.
 *
 * Behaves like ``useRequireAuth`` for unauthenticated visitors. Once
 * the session is in hand, fetches /me/onboarding once and redirects
 * to /welcome when the user hasn't finished — and hasn't explicitly
 * skipped — the four-step flow. Existing users who finished before
 * the flow shipped get their state seeded as ``completed`` server-side,
 * so this only catches accounts that genuinely never went through.
 *
 * The fetch is fire-and-forget: if the call fails (e.g. transient
 * network), we leave the visitor on the page rather than bouncing
 * them to /welcome on a stale assumption.
 */
export function useRequireOnboarded(): AuthState {
  const auth = useRequireAuth();
  const router = useRouter();

  useEffect(() => {
    if (auth.isLoading || !auth.isAuthenticated || !auth.token) return;

    let cancelled = false;
    void (async () => {
      try {
        const { getOnboardingState } = await import("@/lib/api/onboarding");
        const state = await getOnboardingState(auth.token!);
        if (cancelled) return;
        if (!state.completed && state.skipped_entirely_at === null) {
          router.replace("/welcome");
        }
      } catch {
        /* best-effort — leave the user on the page if the call fails */
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [auth.isAuthenticated, auth.isLoading, auth.token, router]);

  return auth;
}
