/**
 * Client-side auth context and hooks.
 *
 * The frontend stores the JWT issued by the Flask `/api/v1/auth/*`
 * endpoints (Spotify OAuth, not yet wired) in `localStorage`.
 * Reloading the app rehydrates the context and fetches `/me` so
 * components can read the current user without threading a prop.
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
  useState,
  type ReactNode,
} from "react";

import { ApiRequestError } from "@/lib/api/client";
import { getMe } from "@/lib/api/me";
import type { User } from "@/types";

const TOKEN_STORAGE_KEY = "greenroom.token";

interface AuthState {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (token: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token === null) {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } else {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    }
  } catch {
    /* storage may be disabled; fail quietly */
  }
}

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const fetchUser = useCallback(async (activeToken: string): Promise<void> => {
    try {
      const fetched = await getMe(activeToken);
      setUser(fetched);
    } catch (err) {
      // A 401/403 here means the stored token is no longer valid —
      // drop it so the UI reverts to the logged-out state.
      if (err instanceof ApiRequestError && (err.status === 401 || err.status === 403)) {
        writeStoredToken(null);
        setToken(null);
      }
      setUser(null);
    }
  }, []);

  useEffect(() => {
    const stored = readStoredToken();
    if (!stored) {
      setIsLoading(false);
      return;
    }
    setToken(stored);
    void fetchUser(stored).finally(() => setIsLoading(false));
  }, [fetchUser]);

  const login = useCallback(
    async (nextToken: string): Promise<void> => {
      writeStoredToken(nextToken);
      setToken(nextToken);
      setIsLoading(true);
      await fetchUser(nextToken);
      setIsLoading(false);
    },
    [fetchUser],
  );

  const logout = useCallback((): void => {
    writeStoredToken(null);
    setToken(null);
    setUser(null);
  }, []);

  const refresh = useCallback(async (): Promise<void> => {
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
      refresh,
    }),
    [user, token, isLoading, login, logout, refresh],
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
