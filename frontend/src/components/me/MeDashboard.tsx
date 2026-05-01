/**
 * Consolidated mobile-first account dashboard at ``/me``.
 *
 * Replaces the per-route hops (For You / Saved / Settings) that the old
 * five-tab bottom nav surfaced individually. One page, four sections:
 *
 *   1. Your Picks — the top three recommendations with a deep-link to
 *      the full /for-you grid.
 *   2. Saved Shows — first three saved events with a link to /saved.
 *   3. Followed — the existing FollowingSections artist/venue lists.
 *   4. Account — Settings link + Sign out.
 *
 * The data is read on mount from the same APIs the dedicated routes use,
 * which means an unsave on /me flips the heart on browse cards instantly
 * via the shared SavedEventsContext, just like /saved does.
 */

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import EventCard from "@/components/events/EventCard";
import RecommendationCard from "@/components/recommendations/RecommendationCard";
import { FollowingSections } from "@/components/settings/FollowingSections";
import { listRecommendations } from "@/lib/api/recommendations";
import { useAuth } from "@/lib/auth";
import { useSavedEvents } from "@/lib/saved-events-context";
import type { Recommendation } from "@/types";

const PICKS_LIMIT = 3;
const SAVED_PREVIEW_LIMIT = 3;

type PicksStatus = "idle" | "loading" | "ready" | "empty" | "error";

interface Props {
  /** Display name shown in the page heading; falls back to "you". */
  displayName: string;
  /** Session JWT, used by the recommendation + follow API calls. */
  token: string;
}

/**
 * Renders the /me dashboard for an authenticated user.
 *
 * Args:
 *     displayName: Friendly name to greet the visitor.
 *     token: Active session JWT for authenticated API calls.
 *
 * Returns:
 *     The full /me dashboard layout.
 */
export default function MeDashboard({ displayName, token }: Props): JSX.Element {
  const router = useRouter();
  const { logout } = useAuth();
  const { savedEvents, isReady: isSavedReady } = useSavedEvents();

  const [picks, setPicks] = useState<Recommendation[]>([]);
  const [picksStatus, setPicksStatus] = useState<PicksStatus>("idle");

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<void> => {
      setPicksStatus("loading");
      try {
        const page = await listRecommendations(token, { perPage: PICKS_LIMIT });
        if (cancelled) return;
        setPicks(page.data);
        setPicksStatus(page.data.length > 0 ? "ready" : "empty");
      } catch {
        if (cancelled) return;
        setPicksStatus("error");
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleSignOut = useCallback((): void => {
    logout();
    router.replace("/");
  }, [logout, router]);

  const savedPreview = savedEvents.slice(0, SAVED_PREVIEW_LIMIT);

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">
      <header className="mb-8">
        <p className="text-sm font-semibold uppercase tracking-widest text-accent">
          Your Greenroom
        </p>
        <h1 className="mt-1 text-3xl font-semibold text-text-primary">
          Hey, {displayName}
        </h1>
      </header>

      <Section
        title="Your picks"
        href="/for-you"
        cta="See all picks →"
      >
        {picksStatus === "loading" || picksStatus === "idle" ? (
          <p className="text-sm text-text-secondary">Loading your picks…</p>
        ) : picksStatus === "error" ? (
          <p className="text-sm text-blush-accent" role="alert">
            We couldn&apos;t load your picks just now.
          </p>
        ) : picksStatus === "empty" ? (
          <EmptyHint
            text="No picks yet. Connect Spotify or pick a few genres in"
            linkText="Settings"
            href="/settings"
          />
        ) : (
          <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {picks.map((rec) => (
              <li key={rec.id}>
                <RecommendationCard recommendation={rec} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Saved shows"
        href="/saved"
        cta={savedEvents.length > SAVED_PREVIEW_LIMIT ? "See all saved →" : undefined}
      >
        {!isSavedReady ? (
          <p className="text-sm text-text-secondary">Loading your saved shows…</p>
        ) : savedPreview.length === 0 ? (
          <EmptyHint
            text="No saved shows yet. Tap the heart on any card in"
            linkText="Events"
            href="/events"
          />
        ) : (
          <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {savedPreview.map((entry) => (
              <li key={entry.event.id}>
                <EventCard event={entry.event} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Following">
        <FollowingSections token={token} />
      </Section>

      <section className="mt-12 border-t border-border pt-8">
        <h2 className="text-base font-semibold text-text-primary">Account</h2>
        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
          <Link
            href="/settings"
            className="rounded-md border border-border bg-bg-white px-4 py-2 text-sm font-medium text-text-primary hover:border-accent hover:text-accent"
          >
            Settings
          </Link>
          <button
            type="button"
            onClick={handleSignOut}
            className="rounded-md px-4 py-2 text-sm font-medium text-blush-accent hover:bg-blush-soft/60"
          >
            Sign out
          </button>
        </div>
      </section>
    </main>
  );
}

interface SectionProps {
  title: string;
  href?: string;
  cta?: string;
  children: React.ReactNode;
}

/**
 * Wraps a /me section with a heading row that optionally deep-links to
 * the full standalone page (e.g. /for-you, /saved).
 *
 * Args:
 *     title: Section heading text.
 *     href: Optional destination for the trailing CTA link.
 *     cta: Optional link label; suppressed if absent.
 *     children: Section body.
 *
 * Returns:
 *     A `<section>` element styled as a /me dashboard block.
 */
function Section({ title, href, cta, children }: SectionProps): JSX.Element {
  return (
    <section className="mt-10 first:mt-0">
      <div className="mb-4 flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
        {href && cta ? (
          <Link
            href={href}
            className="text-sm font-medium text-accent hover:underline"
          >
            {cta}
          </Link>
        ) : null}
      </div>
      {children}
    </section>
  );
}

interface EmptyHintProps {
  text: string;
  linkText: string;
  href: string;
}

/**
 * Renders a one-line empty-state hint with an inline deep-link.
 *
 * Args:
 *     text: Lead-in copy ending right before the inline link.
 *     linkText: Visible label for the inline link.
 *     href: Destination of the inline link.
 *
 * Returns:
 *     A muted paragraph with an underlined inline link.
 */
function EmptyHint({ text, linkText, href }: EmptyHintProps): JSX.Element {
  return (
    <p className="text-sm text-text-secondary">
      {text}{" "}
      <Link
        href={href}
        className="text-text-primary underline underline-offset-2"
      >
        {linkText}
      </Link>
      .
    </p>
  );
}
