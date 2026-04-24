/**
 * Unified "Around this venue" card — client component.
 *
 * Replaces the three separate widgets (static map, nearby POIs, tips)
 * that previously stacked on the venue page. The card has:
 *
 * * A compact Apple Maps snapshot at the top. Clicking it opens the
 *   {@link VenueSurroundingsModal} with a full-bleed interactive map.
 * * A tab strip with "Tips" (default) and "Nearby" panels.
 * * A "Leave a tip" button inside the Tips tab that toggles the form,
 *   so signed-in users can drop a recommendation without the form
 *   taking up screen real estate by default.
 *
 * All network calls and voting UX are preserved from the previous
 * ``VenueMapTips`` component. The snapshot URL and the nearby POIs are
 * pre-fetched on the server so the first paint of the card already
 * ships HTML content (important for AI crawlers).
 */

"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import EmptyState from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { ApiRequestError } from "@/lib/api/client";
import {
  listVenueTips,
  searchNearbyPlaces,
  submitMapRecommendation,
  voteOnMapRecommendation,
  type NearbyPlace,
} from "@/lib/api/maps";
import type { NearbyPoi, VenueMapSnapshot } from "@/lib/api/venues";
import { useAuth } from "@/lib/auth";
import { getGuestSessionId } from "@/lib/guest-session";
import type { MapRecommendation } from "@/types";

import VenueSurroundingsModal from "./VenueSurroundingsModal";

const MAX_BODY_LEN = 500;
const SEARCH_DEBOUNCE_MS = 250;

const CATEGORY_OPTIONS: Array<{ value: TipCategory; label: string }> = [
  { value: "food", label: "Food" },
  { value: "drinks", label: "Drinks" },
];

type TipCategory = "food" | "drinks";
type Tab = "tips" | "nearby";

interface VenueSurroundingsProps {
  slug: string;
  venueId: string;
  venueName: string;
  venueAddress: string | null;
  latitude: number;
  longitude: number;
  snapshot: VenueMapSnapshot | null;
  nearbyPois: NearbyPoi[];
}

/**
 * Render the unified surroundings card with tip and nearby panels.
 *
 * @param slug - Venue slug (used for tip fetching).
 * @param venueId - Venue UUID (used when submitting a new tip).
 * @param venueName - Display name for headings, modal title, and pin label.
 * @param venueAddress - Street address, passed to the modal's list panel.
 * @param latitude - Venue latitude (used for tip autocomplete anchoring).
 * @param longitude - Venue longitude.
 * @param snapshot - Pre-fetched Apple Maps snapshot, or ``null`` if the
 *     backend is unavailable.
 * @param nearbyPois - Pre-fetched Apple-backed nearby POIs.
 */
export default function VenueSurroundings({
  slug,
  venueId,
  venueName,
  venueAddress,
  latitude,
  longitude,
  snapshot,
  nearbyPois,
}: VenueSurroundingsProps): JSX.Element {
  const { token, isAuthenticated } = useAuth();
  const { show: showToast } = useToast();
  const [activeTab, setActiveTab] = useState<Tab>("tips");
  const [modalOpen, setModalOpen] = useState<boolean>(false);
  const [showTipForm, setShowTipForm] = useState<boolean>(false);

  const [tips, setTips] = useState<MapRecommendation[]>([]);
  const [tipCategory, setTipCategory] = useState<TipCategory | "all">("all");
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const guestSessionId = useMemo(
    () => (isAuthenticated ? null : getGuestSessionId()),
    [isAuthenticated],
  );

  const refresh = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const rows = await listVenueTips(slug, token, {
        category: tipCategory === "all" ? undefined : tipCategory,
        sessionId: guestSessionId ?? undefined,
      });
      setTips(rows);
    } catch (err) {
      setLoadError(
        err instanceof ApiRequestError
          ? err.message
          : "Could not load tips.",
      );
    } finally {
      setIsLoading(false);
    }
  }, [slug, token, tipCategory, guestSessionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleVote = useCallback(
    async (tip: MapRecommendation, nextValue: -1 | 1): Promise<void> => {
      const effective: -1 | 0 | 1 =
        tip.viewer_vote === nextValue ? 0 : nextValue;
      const previous = tips;
      setTips((list) =>
        list.map((t) => applyOptimisticVote(t, tip.id, effective)),
      );
      try {
        const result = await voteOnMapRecommendation(
          tip.id,
          token,
          effective,
          guestSessionId,
        );
        setTips((list) =>
          list.map((t) =>
            t.id === tip.id
              ? {
                  ...t,
                  likes: result.likes,
                  dislikes: result.dislikes,
                  viewer_vote: result.viewer_vote,
                }
              : t,
          ),
        );
      } catch (err) {
        setTips(previous);
        const message =
          err instanceof ApiRequestError
            ? err.message
            : "Could not record your vote.";
        showToast(message);
      }
    },
    [tips, token, guestSessionId, showToast],
  );

  return (
    <>
      <section
        id="tips"
        aria-labelledby="venue-surroundings-heading"
        className="flex scroll-mt-20 flex-col overflow-hidden rounded-lg border border-border bg-bg-white"
      >
        <h2 id="venue-surroundings-heading" className="sr-only">
          Around {venueName}
        </h2>

        {snapshot ? (
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            aria-label={`Open interactive map around ${venueName}`}
            className="group relative block h-[140px] w-full overflow-hidden bg-bg-surface"
          >
            <img
              src={snapshot.url}
              alt={`Map around ${venueName}`}
              className="block h-full w-full object-cover transition-transform group-hover:scale-[1.02]"
              loading="lazy"
              decoding="async"
            />
            <span className="absolute bottom-2 right-2 rounded-full bg-bg-white/95 px-3 py-1 text-xs font-medium text-text-primary shadow-sm">
              Expand map →
            </span>
          </button>
        ) : null}

        <div className="flex flex-col gap-4 p-4">
          <div
            role="tablist"
            aria-label="Surroundings view"
            className="flex items-center gap-1 border-b border-border"
          >
            <TabButton
              tab="tips"
              active={activeTab === "tips"}
              onSelect={setActiveTab}
              label="Tips"
              count={tips.length}
            />
            <TabButton
              tab="nearby"
              active={activeTab === "nearby"}
              onSelect={setActiveTab}
              label="Nearby"
              count={nearbyPois.length}
            />
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="ml-auto text-xs font-medium text-green-primary hover:underline"
            >
              View on map →
            </button>
          </div>

          {activeTab === "tips" ? (
            <div
              role="tabpanel"
              id="panel-tips"
              aria-labelledby="tab-tips"
              className="flex flex-col gap-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CategoryToggle
                  current={tipCategory}
                  onSelect={setTipCategory}
                />
                {isAuthenticated && token ? (
                  <button
                    type="button"
                    onClick={() => setShowTipForm((v) => !v)}
                    aria-expanded={showTipForm}
                    aria-controls="leave-tip-form"
                    className="rounded-md bg-green-primary px-3 py-1.5 text-xs font-semibold text-text-inverse transition hover:bg-green-dark"
                  >
                    {showTipForm ? "Cancel" : "+ Leave a tip"}
                  </button>
                ) : null}
              </div>

              {!isAuthenticated ? (
                <p className="rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-text-secondary">
                  Sign in to drop a tip on the map. Reading and voting
                  don&apos;t require an account.
                </p>
              ) : null}

              {isAuthenticated && token && showTipForm ? (
                <LeaveTipForm
                  venueId={venueId}
                  token={token}
                  anchorLat={latitude}
                  anchorLng={longitude}
                  onPosted={async () => {
                    setShowTipForm(false);
                    await refresh();
                  }}
                />
              ) : null}

              {loadError ? (
                <p className="rounded-md border border-blush-accent/40 bg-blush-soft px-3 py-2 text-sm text-[#7A3028]">
                  {loadError}
                </p>
              ) : null}

              {isLoading ? (
                <p className="text-sm text-text-secondary">Loading tips…</p>
              ) : tips.length === 0 ? (
                <EmptyState
                  title="No tips yet"
                  description={`Be the first to share a food or drink spot near ${venueName}.`}
                />
              ) : (
                <ul className="flex flex-col gap-3">
                  {tips.map((tip) => (
                    <li key={tip.id}>
                      <TipItem tip={tip} onVote={handleVote} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <div
              role="tabpanel"
              id="panel-nearby"
              aria-labelledby="tab-nearby"
              className="flex flex-col gap-3"
            >
              {nearbyPois.length === 0 ? (
                <EmptyState
                  title="No nearby spots"
                  description={`We couldn't find any bars or restaurants within walking distance of ${venueName}.`}
                />
              ) : (
                <>
                  <p className="text-sm text-text-secondary">
                    Bars, restaurants, and cafes within a short walk.
                  </p>
                  <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {nearbyPois.map((poi) => (
                      <li
                        key={`${poi.name}-${poi.latitude}-${poi.longitude}`}
                        className="flex items-start justify-between gap-3 rounded-md border border-border bg-bg-white px-3 py-2"
                      >
                        <div className="flex min-w-0 flex-col">
                          <span className="truncate text-sm font-medium text-text-primary">
                            {poi.name}
                          </span>
                          <span className="truncate text-xs text-text-secondary">
                            {poi.category}
                            {poi.address ? ` · ${poi.address}` : ""}
                          </span>
                        </div>
                        <span className="shrink-0 text-xs font-medium text-text-secondary">
                          {formatDistance(poi.distance_m)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      </section>

      {modalOpen ? (
        <VenueSurroundingsModal
          venueName={venueName}
          venueLatitude={latitude}
          venueLongitude={longitude}
          venueAddress={venueAddress}
          tips={tips}
          nearbyPois={nearbyPois}
          onClose={() => setModalOpen(false)}
        />
      ) : null}
    </>
  );
}

interface TabButtonProps {
  tab: Tab;
  active: boolean;
  onSelect: (tab: Tab) => void;
  label: string;
  count: number;
}

function TabButton({
  tab,
  active,
  onSelect,
  label,
  count,
}: TabButtonProps): JSX.Element {
  return (
    <button
      type="button"
      role="tab"
      id={`tab-${tab}`}
      aria-selected={active}
      aria-controls={`panel-${tab}`}
      onClick={() => onSelect(tab)}
      className={
        "flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition " +
        (active
          ? "border-green-primary text-text-primary"
          : "border-transparent text-text-secondary hover:text-text-primary")
      }
    >
      <span>{label}</span>
      <span
        className={
          "inline-flex min-w-[1.5rem] items-center justify-center rounded-full px-1.5 text-xs " +
          (active
            ? "bg-green-primary text-text-inverse"
            : "bg-bg-surface text-text-secondary")
        }
      >
        {count}
      </span>
    </button>
  );
}

interface CategoryToggleProps {
  current: TipCategory | "all";
  onSelect: (next: TipCategory | "all") => void;
}

function CategoryToggle({
  current,
  onSelect,
}: CategoryToggleProps): JSX.Element {
  return (
    <div
      role="tablist"
      aria-label="Filter by category"
      className="inline-flex overflow-hidden rounded-md border border-border text-xs"
    >
      {(["all", "food", "drinks"] as const).map((option) => (
        <button
          key={option}
          role="tab"
          aria-selected={current === option}
          type="button"
          onClick={() => onSelect(option)}
          className={
            "px-3 py-1.5 capitalize transition " +
            (current === option
              ? "bg-green-primary text-text-inverse"
              : "bg-bg-white text-text-secondary hover:text-text-primary")
          }
        >
          {option}
        </button>
      ))}
    </div>
  );
}

interface LeaveTipFormProps {
  venueId: string;
  token: string;
  anchorLat: number;
  anchorLng: number;
  onPosted: () => void | Promise<void>;
}

function LeaveTipForm({
  venueId,
  token,
  anchorLat,
  anchorLng,
  onPosted,
}: LeaveTipFormProps): JSX.Element {
  const { show: showToast } = useToast();
  const [category, setCategory] = useState<TipCategory>("food");
  const [body, setBody] = useState<string>("");
  const [query, setQuery] = useState<string>("");
  const [options, setOptions] = useState<NearbyPlace[]>([]);
  const [isSearching, setIsSearching] = useState<boolean>(false);
  const [pickedPlace, setPickedPlace] = useState<NearbyPlace | null>(null);
  const [honeypot, setHoneypot] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const searchSeq = useRef<number>(0);

  useEffect(() => {
    if (pickedPlace && pickedPlace.name === query) return;
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setOptions([]);
      return;
    }
    const seq = ++searchSeq.current;
    const handle = setTimeout(async () => {
      setIsSearching(true);
      try {
        const results = await searchNearbyPlaces(
          {
            latitude: anchorLat,
            longitude: anchorLng,
            q: trimmed,
            categories:
              category === "drinks"
                ? ["Bar", "Cafe"]
                : ["Restaurant", "Cafe"],
            radiusM: 1000,
            limit: 8,
          },
          token,
        );
        if (seq === searchSeq.current) setOptions(results);
      } catch {
        if (seq === searchSeq.current) setOptions([]);
      } finally {
        if (seq === searchSeq.current) setIsSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query, category, anchorLat, anchorLng, token, pickedPlace]);

  const handlePick = useCallback((place: NearbyPlace): void => {
    setPickedPlace(place);
    setQuery(place.name);
    setOptions([]);
  }, []);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>): Promise<void> => {
      event.preventDefault();
      if (!pickedPlace) {
        showToast("Pick a place from the list first.");
        return;
      }
      const trimmed = body.trim();
      if (trimmed.length < 2) {
        showToast("Tip is too short.");
        return;
      }
      if (trimmed.length > MAX_BODY_LEN) {
        showToast(`Tip exceeds the ${MAX_BODY_LEN}-character limit.`);
        return;
      }
      setIsSubmitting(true);
      try {
        await submitMapRecommendation(
          {
            query: pickedPlace.name,
            by: "name",
            venueId,
            category,
            body: trimmed,
            honeypot,
          },
          token,
        );
        setBody("");
        setQuery("");
        setPickedPlace(null);
        setHoneypot("");
        await onPosted();
      } catch (err) {
        const message =
          err instanceof ApiRequestError
            ? err.message
            : "Could not post tip.";
        showToast(message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [
      pickedPlace,
      body,
      honeypot,
      venueId,
      category,
      token,
      onPosted,
      showToast,
    ],
  );

  const remaining = MAX_BODY_LEN - body.length;

  return (
    <form
      id="leave-tip-form"
      onSubmit={handleSubmit}
      className="flex flex-col gap-2 rounded-lg border border-border bg-bg-surface p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor="tip-category"
          className="text-xs font-medium text-text-secondary"
        >
          Category
        </label>
        <select
          id="tip-category"
          value={category}
          onChange={(e: ChangeEvent<HTMLSelectElement>) => {
            setCategory(e.target.value as TipCategory);
            setPickedPlace(null);
          }}
          className="rounded-md border border-border bg-bg-white px-2 py-1 text-sm"
        >
          {CATEGORY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="relative">
        <input
          type="text"
          aria-label="Place name"
          placeholder="Search for a nearby spot"
          autoComplete="off"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPickedPlace(null);
          }}
          className="w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm text-text-primary focus:border-green-primary focus:outline-none"
        />
        {isSearching ? (
          <span className="absolute right-3 top-2 text-xs text-text-secondary">
            Searching…
          </span>
        ) : null}
        {options.length > 0 ? (
          <ul
            role="listbox"
            aria-label="Place suggestions"
            className="absolute z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border bg-bg-white shadow-md"
          >
            {options.map((place) => (
              <li key={`${place.name}-${place.latitude},${place.longitude}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={pickedPlace?.name === place.name}
                  onClick={() => handlePick(place)}
                  className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-bg-surface"
                >
                  <span className="font-medium text-text-primary">
                    {place.name}
                  </span>
                  <span className="text-xs text-text-secondary">
                    {place.category ?? ""}
                    {place.category && place.address ? " · " : ""}
                    {place.address ?? ""}
                    {" · "}
                    {place.distance_m} m away
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <textarea
        aria-label="Tip body"
        placeholder={`What's good about this spot before or after a show at this venue?`}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        maxLength={MAX_BODY_LEN + 100}
        className="w-full resize-y rounded-md border border-border bg-bg-white px-3 py-2 text-sm text-text-primary focus:border-green-primary focus:outline-none"
      />

      <label
        aria-hidden="true"
        style={{
          position: "absolute",
          left: "-9999px",
          width: "1px",
          height: "1px",
          overflow: "hidden",
        }}
      >
        Website
        <input
          tabIndex={-1}
          autoComplete="off"
          value={honeypot}
          onChange={(e) => setHoneypot(e.target.value)}
        />
      </label>

      <div className="flex items-center justify-between">
        <span
          className={
            "text-xs " +
            (remaining < 0 ? "text-blush-accent" : "text-text-secondary")
          }
        >
          {remaining} characters left
        </span>
        <button
          type="submit"
          disabled={
            isSubmitting || pickedPlace === null || body.trim().length < 2
          }
          className="rounded-md bg-green-primary px-3 py-1.5 text-sm font-semibold text-text-inverse transition disabled:opacity-50 hover:bg-green-dark"
        >
          {isSubmitting ? "Posting…" : "Post tip"}
        </button>
      </div>
    </form>
  );
}

interface TipItemProps {
  tip: MapRecommendation;
  onVote: (tip: MapRecommendation, value: -1 | 1) => Promise<void>;
}

function TipItem({ tip, onVote }: TipItemProps): JSX.Element {
  const net = tip.likes - tip.dislikes;
  return (
    <article className="flex gap-3 rounded-lg border border-border bg-bg-white p-3">
      <div className="flex w-10 flex-col items-center gap-1 text-xs text-text-secondary">
        <VoteButton
          direction="up"
          active={tip.viewer_vote === 1}
          onClick={() => onVote(tip, 1)}
        />
        <span
          aria-label={`${net} net votes`}
          className="font-semibold text-text-primary"
        >
          {net}
        </span>
        <VoteButton
          direction="down"
          active={tip.viewer_vote === -1}
          onClick={() => onVote(tip, -1)}
        />
      </div>
      <div className="flex flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
          <span className="font-semibold text-text-primary">
            {tip.place_name}
          </span>
          <span className="inline-flex items-center rounded-full bg-bg-surface px-2 py-0.5 capitalize">
            {tip.category}
          </span>
          {typeof tip.distance_from_venue_m === "number" ? (
            <span>{tip.distance_from_venue_m} m away</span>
          ) : null}
        </div>
        <p className="whitespace-pre-wrap text-sm text-text-primary">
          {tip.body}
        </p>
        {tip.place_address ? (
          <a
            href={buildDirectionsUrl(tip)}
            className="text-xs font-medium text-green-primary hover:underline"
          >
            Get directions →
          </a>
        ) : null}
      </div>
    </article>
  );
}

/**
 * Build an Apple Maps deep link that opens walking directions to the
 * tip's pin.
 *
 * @param tip - The recommendation to route to.
 * @returns A URL string suitable for an anchor ``href``.
 */
function buildDirectionsUrl(tip: MapRecommendation): string {
  const daddr = tip.place_address ?? `${tip.latitude},${tip.longitude}`;
  const params = new URLSearchParams({ daddr, dirflg: "w" });
  return `https://maps.apple.com/?${params.toString()}`;
}

interface VoteButtonProps {
  direction: "up" | "down";
  active: boolean;
  onClick: () => void;
}

function VoteButton({
  direction,
  active,
  onClick,
}: VoteButtonProps): JSX.Element {
  const label = direction === "up" ? "Upvote" : "Downvote";
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={
        "flex h-6 w-6 items-center justify-center rounded-full border transition " +
        (active
          ? "border-green-primary bg-green-primary text-text-inverse"
          : "border-border text-text-secondary hover:border-green-primary hover:text-text-primary")
      }
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 12 12"
        width={12}
        height={12}
        aria-hidden="true"
        fill="currentColor"
      >
        {direction === "up" ? (
          <path d="M6 2l4 6H2z" />
        ) : (
          <path d="M6 10L2 4h8z" />
        )}
      </svg>
    </button>
  );
}

/**
 * Recompute a tip's like/dislike counts when the viewer flips their
 * vote. Mirrors ``VenueComments.applyOptimisticVote`` so both widgets
 * feel identical.
 *
 * @param tip - The current tip row.
 * @param targetId - The id of the tip whose vote is changing.
 * @param newValue - The viewer's new vote state.
 * @returns The tip with updated counts/viewer_vote, or the original
 *     when the row is not the target.
 */
function applyOptimisticVote(
  tip: MapRecommendation,
  targetId: string,
  newValue: -1 | 0 | 1,
): MapRecommendation {
  if (tip.id !== targetId) return tip;
  const prev = tip.viewer_vote ?? 0;
  if (prev === newValue) return tip;
  let likes = tip.likes;
  let dislikes = tip.dislikes;
  if (prev === 1) likes -= 1;
  if (prev === -1) dislikes -= 1;
  if (newValue === 1) likes += 1;
  if (newValue === -1) dislikes += 1;
  const next: MapRecommendation["viewer_vote"] =
    newValue === 0 ? null : newValue;
  return { ...tip, likes, dislikes, viewer_vote: next };
}

/**
 * Format a walking distance for the Nearby tab list.
 *
 * @param meters - Distance in meters.
 * @returns Short display string, e.g. ``"120 m"`` or ``"1.2 km"``.
 */
function formatDistance(meters: number): string {
  if (meters < 100) return `${meters} m`;
  if (meters < 1000) return `${Math.round(meters / 10) * 10} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}
