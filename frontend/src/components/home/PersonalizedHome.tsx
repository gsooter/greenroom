/**
 * Client island that renders the personalized home page sections for
 * signed-in users.
 *
 * Lives above the server-rendered "Browse the calendar" section in
 * ``app/page.tsx``. Anonymous and unauthenticated visitors render
 * nothing here — the SSR shell already gives them the hero copy and
 * the public events list, which crawlers can index.
 *
 * Branching:
 *
 * * No auth token → returns null (the SSR hero takes over).
 * * Authenticated, payload still loading → skeleton placeholders.
 * * Authenticated, ``has_signal === false`` → welcome prompt to
 *   connect a music service or follow some artists.
 * * Authenticated, ``has_signal === true`` → Section 1
 *   (recommendations w/ reasons) and Section 2 (new-since-last-visit
 *   w/ NEW badges) above the SSR'd browse view.
 */

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import EventCard from "@/components/events/EventCard";
import RecommendationCard from "@/components/recommendations/RecommendationCard";
import RecommendationGridSkeleton from "@/components/recommendations/RecommendationGridSkeleton";
import { getHome } from "@/lib/api/home";
import { useAuth } from "@/lib/auth";
import type { EventSummary, HomePayload, Recommendation } from "@/types";

type Status = "idle" | "loading" | "ready" | "error";

const NEW_SECTION_INLINE_LIMIT = 4;

export default function PersonalizedHome(): JSX.Element | null {
  const { isAuthenticated, isLoading: authLoading, token, user } = useAuth();
  const [status, setStatus] = useState<Status>("idle");
  const [payload, setPayload] = useState<HomePayload | null>(null);

  useEffect(() => {
    if (authLoading) return;
    if (!token || !isAuthenticated) {
      setStatus("idle");
      setPayload(null);
      return;
    }

    let cancelled = false;
    setStatus("loading");
    void getHome(token)
      .then((data) => {
        if (cancelled) return;
        setPayload(data);
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, [authLoading, isAuthenticated, token]);

  if (authLoading) return null;
  if (!isAuthenticated || !token) return null;

  if (status === "loading" || status === "idle") {
    return (
      <section className="flex flex-col gap-4 pb-10">
        <header className="flex flex-col gap-1">
          <h2 className="text-xl font-semibold">Coming up that you&apos;ll care about</h2>
          <p className="text-sm text-text-secondary">
            Picked from artists you follow and your listening history.
          </p>
        </header>
        <RecommendationGridSkeleton />
      </section>
    );
  }

  if (status === "error" || payload === null) {
    return null;
  }

  if (!payload.has_signal) {
    return <WelcomePrompt displayName={user?.display_name ?? null} />;
  }

  const tooThin =
    payload.recommendations.length + payload.popularity_fallback.length < 3;
  if (tooThin) {
    return <ThinSignalPrompt />;
  }

  return (
    <>
      <RecommendationsSection
        recommendations={payload.recommendations}
        popularityFallback={payload.popularity_fallback}
      />
      {payload.new_since_last_visit.length > 0 ? (
        <NewSinceLastVisitSection events={payload.new_since_last_visit} />
      ) : null}
    </>
  );
}

function RecommendationsSection({
  recommendations,
  popularityFallback,
}: {
  recommendations: Recommendation[];
  popularityFallback: EventSummary[];
}): JSX.Element {
  return (
    <section className="flex flex-col gap-4 pb-10" data-testid="home-section-recs">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h2 className="text-xl font-semibold">
            Coming up that you&apos;ll care about
          </h2>
          <p className="text-sm text-text-secondary">
            Picked from artists you follow and your listening history.
          </p>
        </div>
        <Link
          href="/for-you"
          className="text-sm font-medium text-accent hover:underline"
        >
          See all →
        </Link>
      </header>

      <ul className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {recommendations.map((rec) => (
          <li key={rec.id}>
            <RecommendationCard recommendation={rec} />
          </li>
        ))}
        {popularityFallback.map((event) => (
          <li key={`fallback-${event.id}`} className="flex flex-col gap-2">
            <EventCard event={event} />
            <span className="self-start rounded-full bg-bg-surface px-3 py-1 text-xs font-medium text-text-secondary">
              Popular in DC
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function NewSinceLastVisitSection({
  events,
}: {
  events: EventSummary[];
}): JSX.Element {
  const inline = events.slice(0, NEW_SECTION_INLINE_LIMIT);
  const remaining = Math.max(0, events.length - NEW_SECTION_INLINE_LIMIT);

  return (
    <section
      className="flex flex-col gap-4 pb-10"
      data-testid="home-section-new"
    >
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h2 className="text-xl font-semibold">New since your last visit</h2>
          <p className="text-sm text-text-secondary">
            Just announced for artists you care about.
          </p>
        </div>
        {remaining > 0 ? (
          <Link
            href="/for-you"
            className="text-sm font-medium text-accent hover:underline"
          >
            See all ({events.length}) →
          </Link>
        ) : null}
      </header>

      <ul
        className={
          inline.length <= 3
            ? "flex flex-wrap gap-4"
            : "grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
        }
      >
        {inline.map((event) => (
          <li
            key={event.id}
            className={`relative ${inline.length <= 3 ? "min-w-[260px] flex-1" : ""}`}
          >
            <span
              className="absolute left-3 top-3 z-30 rounded-full bg-blush-soft px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-blush-accent"
              data-testid="home-new-badge"
            >
              New
            </span>
            <EventCard event={event} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function WelcomePrompt({
  displayName,
}: {
  displayName: string | null;
}): JSX.Element {
  const greeting = displayName ? `Welcome, ${displayName}` : "Welcome to Greenroom";

  return (
    <section
      className="flex flex-col gap-4 rounded-xl border border-border bg-bg-surface p-6 sm:p-8"
      data-testid="home-section-welcome"
    >
      <h2 className="text-2xl font-semibold text-text-primary">{greeting}</h2>
      <p className="max-w-2xl text-sm text-text-secondary">
        Let&apos;s make this app know what you love. Connect a music service
        for instant taste matching, or follow some artists to start tuning your
        recommendations.
      </p>
      <div className="flex flex-wrap gap-3">
        <Link
          href="/settings"
          className="rounded-md bg-green-primary px-4 py-2 text-sm font-semibold text-text-inverse hover:opacity-90"
        >
          Connect Apple Music
        </Link>
        <Link
          href="/settings"
          className="rounded-md border border-border bg-bg-white px-4 py-2 text-sm font-semibold text-text-primary hover:border-green-primary"
        >
          Connect Tidal
        </Link>
        <Link
          href="/welcome"
          className="rounded-md border border-border bg-bg-white px-4 py-2 text-sm font-semibold text-text-primary hover:border-green-primary"
        >
          Browse artists to follow →
        </Link>
      </div>
    </section>
  );
}

function ThinSignalPrompt(): JSX.Element {
  return (
    <section
      className="flex flex-col gap-3 rounded-xl border border-border bg-bg-surface p-6"
      data-testid="home-section-thin-signal"
    >
      <p className="text-sm text-text-primary">
        We&apos;re learning your taste — connect a music service or follow more
        artists for stronger recommendations.
      </p>
      <div className="flex flex-wrap gap-3">
        <Link
          href="/settings"
          className="rounded-md bg-green-primary px-4 py-2 text-sm font-semibold text-text-inverse hover:opacity-90"
        >
          Connect Apple Music
        </Link>
        <Link
          href="/welcome"
          className="rounded-md border border-border bg-bg-white px-4 py-2 text-sm font-semibold text-text-primary hover:border-green-primary"
        >
          Browse artists →
        </Link>
      </div>
    </section>
  );
}
