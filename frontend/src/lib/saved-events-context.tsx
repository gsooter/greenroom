/**
 * Client-side context tracking the signed-in user's saved events.
 *
 * One source of truth for "is this event saved?" across the browse grid,
 * event detail page, and `/saved` view. Fetches once when the session
 * boots and applies optimistic updates on toggle so the heart icon
 * flips immediately without waiting for the round-trip.
 *
 * Anonymous sessions short-circuit — the context stays empty and
 * `toggle` is a no-op so components can render the same tree regardless
 * of auth state.
 */

"use client";

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

import { useAuth } from "@/lib/auth";
import {
  listSavedEvents,
  saveEvent,
  unsaveEvent,
} from "@/lib/api/saved-events";
import type { SavedEvent } from "@/types";

const SAVED_PAGE_SIZE = 100;

interface SavedEventsState {
  savedIds: ReadonlySet<string>;
  savedEvents: SavedEvent[];
  isLoading: boolean;
  isReady: boolean;
  isSaved: (eventId: string) => boolean;
  toggle: (eventId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const SavedEventsContext = createContext<SavedEventsState | null>(null);

export function SavedEventsProvider({
  children,
}: {
  children: ReactNode;
}): JSX.Element {
  const { token, isAuthenticated } = useAuth();
  const [savedEvents, setSavedEvents] = useState<SavedEvent[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isReady, setIsReady] = useState<boolean>(false);

  const fetchAll = useCallback(
    async (activeToken: string): Promise<void> => {
      setIsLoading(true);
      try {
        const accumulated: SavedEvent[] = [];
        let page = 1;
        while (true) {
          const res = await listSavedEvents(activeToken, {
            page,
            perPage: SAVED_PAGE_SIZE,
          });
          accumulated.push(...res.data);
          if (!res.meta.has_next) break;
          page += 1;
        }
        setSavedEvents(accumulated);
        setIsReady(true);
      } catch {
        // Leave prior state in place; a 401 will be cleared by AuthProvider.
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!isAuthenticated || !token) {
      setSavedEvents([]);
      setIsReady(false);
      return;
    }
    void fetchAll(token);
  }, [isAuthenticated, token, fetchAll]);

  const savedIds = useMemo(
    () => new Set(savedEvents.map((entry) => entry.event.id)),
    [savedEvents],
  );

  // Track in-flight toggles so rapid double-clicks don't desync the
  // optimistic state against the server's view of the truth.
  const inflight = useRef<Set<string>>(new Set());

  const toggle = useCallback(
    async (eventId: string): Promise<void> => {
      if (!token || !isAuthenticated) return;
      if (inflight.current.has(eventId)) return;
      inflight.current.add(eventId);

      const wasSaved = savedIds.has(eventId);
      const prev = savedEvents;

      if (wasSaved) {
        setSavedEvents((current) =>
          current.filter((entry) => entry.event.id !== eventId),
        );
      }

      try {
        if (wasSaved) {
          await unsaveEvent(token, eventId);
        } else {
          const fresh = await saveEvent(token, eventId);
          setSavedEvents((current) => {
            if (current.some((entry) => entry.event.id === eventId)) {
              return current;
            }
            return [fresh, ...current];
          });
        }
      } catch {
        setSavedEvents(prev);
      } finally {
        inflight.current.delete(eventId);
      }
    },
    [token, isAuthenticated, savedIds, savedEvents],
  );

  const refresh = useCallback(async (): Promise<void> => {
    if (!token) return;
    await fetchAll(token);
  }, [token, fetchAll]);

  const isSaved = useCallback(
    (eventId: string): boolean => savedIds.has(eventId),
    [savedIds],
  );

  const value = useMemo<SavedEventsState>(
    () => ({
      savedIds,
      savedEvents,
      isLoading,
      isReady,
      isSaved,
      toggle,
      refresh,
    }),
    [savedIds, savedEvents, isLoading, isReady, isSaved, toggle, refresh],
  );

  return (
    <SavedEventsContext.Provider value={value}>
      {children}
    </SavedEventsContext.Provider>
  );
}

export function useSavedEvents(): SavedEventsState {
  const ctx = useContext(SavedEventsContext);
  if (!ctx) {
    throw new Error("useSavedEvents must be called inside <SavedEventsProvider>.");
  }
  return ctx;
}
