/**
 * Venue comments widget — client component.
 *
 * Shown under the upcoming shows section on `/venues/[slug]`. Renders a
 * category tab strip, a top/new sort toggle, the ranked comment list,
 * and an inline composer for signed-in visitors. Signed-out visitors
 * can read and vote (by guest session id) but can't submit.
 *
 * Loads from the backend via `listVenueComments` and re-fetches when
 * filters change. Votes apply optimistically with a rollback on
 * failure so the arrow state feels instant even on slow networks.
 */

"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import EmptyState from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { useAuth } from "@/lib/auth";
import {
  listVenueComments,
  submitVenueComment,
  voteOnVenueComment,
} from "@/lib/api/venue-comments";
import { ApiRequestError } from "@/lib/api/client";
import { getGuestSessionId } from "@/lib/guest-session";
import type {
  VenueComment,
  VenueCommentCategory,
  VenueCommentSort,
} from "@/types";

const CATEGORY_OPTIONS: Array<{ value: VenueCommentCategory; label: string }> =
  [
    { value: "vibes", label: "Vibes" },
    { value: "tickets", label: "Tickets" },
    { value: "safety", label: "Safety" },
    { value: "access", label: "Access" },
    { value: "food_drink", label: "Food & drink" },
    { value: "other", label: "Other" },
  ];

const MAX_BODY_LEN = 2000;

interface VenueCommentsProps {
  slug: string;
}

export default function VenueComments({ slug }: VenueCommentsProps): JSX.Element {
  const { token, isAuthenticated } = useAuth();
  const { show: showToast } = useToast();
  const [comments, setComments] = useState<VenueComment[]>([]);
  const [category, setCategory] = useState<VenueCommentCategory | "all">("all");
  const [sort, setSort] = useState<VenueCommentSort>("top");
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
      const res = await listVenueComments(slug, token, {
        category: category === "all" ? undefined : category,
        sort,
        sessionId: guestSessionId ?? undefined,
      });
      setComments(res.data);
    } catch (err) {
      setLoadError(
        err instanceof ApiRequestError
          ? err.message
          : "Could not load comments.",
      );
    } finally {
      setIsLoading(false);
    }
  }, [slug, token, category, sort, guestSessionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleVote = useCallback(
    async (comment: VenueComment, nextValue: -1 | 1): Promise<void> => {
      const effective: -1 | 0 | 1 =
        comment.viewer_vote === nextValue ? 0 : nextValue;

      // Optimistic update — recompute counts locally.
      const previous = comments;
      setComments((list) =>
        list.map((c) => applyOptimisticVote(c, comment.id, effective)),
      );

      try {
        const result = await voteOnVenueComment(
          slug,
          comment.id,
          token,
          effective,
          guestSessionId,
        );
        setComments((list) =>
          list.map((c) =>
            c.id === comment.id
              ? {
                  ...c,
                  likes: result.likes,
                  dislikes: result.dislikes,
                  viewer_vote: result.viewer_vote,
                }
              : c,
          ),
        );
      } catch (err) {
        setComments(previous);
        const message =
          err instanceof ApiRequestError
            ? err.message
            : "Could not record your vote.";
        showToast(message);
      }
    },
    [comments, slug, token, guestSessionId, showToast],
  );

  return (
    <section
      aria-labelledby="venue-comments-heading"
      className="flex flex-col gap-4"
    >
      <div className="flex items-end justify-between gap-3">
        <h2
          id="venue-comments-heading"
          className="text-xl font-semibold text-text-primary"
        >
          Tips from the community
        </h2>
        <SortToggle sort={sort} onChange={setSort} />
      </div>

      <CategoryTabs current={category} onSelect={setCategory} />

      {isAuthenticated ? (
        <CommentComposer
          slug={slug}
          token={token ?? ""}
          defaultCategory={category === "all" ? "vibes" : category}
          onPosted={refresh}
        />
      ) : (
        <p className="rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-text-secondary">
          Sign in to leave a tip. Reading and voting don&apos;t require an
          account.
        </p>
      )}

      {loadError ? (
        <p className="rounded-md border border-blush-accent/40 bg-blush-soft px-3 py-2 text-sm text-[#7A3028]">
          {loadError}
        </p>
      ) : null}

      {isLoading ? (
        <p className="text-sm text-text-secondary">Loading comments…</p>
      ) : comments.length === 0 ? (
        <EmptyState
          title="No tips yet"
          description="Be the first to share what this venue is like."
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {comments.map((comment) => (
            <li key={comment.id}>
              <CommentItem comment={comment} onVote={handleVote} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

interface SortToggleProps {
  sort: VenueCommentSort;
  onChange: (next: VenueCommentSort) => void;
}

function SortToggle({ sort, onChange }: SortToggleProps): JSX.Element {
  return (
    <div
      role="tablist"
      aria-label="Sort comments"
      className="inline-flex overflow-hidden rounded-md border border-border text-xs"
    >
      {(["top", "new"] as VenueCommentSort[]).map((option) => (
        <button
          key={option}
          role="tab"
          aria-selected={sort === option}
          type="button"
          onClick={() => onChange(option)}
          className={
            "px-3 py-1.5 capitalize transition " +
            (sort === option
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

interface CategoryTabsProps {
  current: VenueCommentCategory | "all";
  onSelect: (next: VenueCommentCategory | "all") => void;
}

function CategoryTabs({ current, onSelect }: CategoryTabsProps): JSX.Element {
  return (
    <div
      role="tablist"
      aria-label="Filter by category"
      className="-mx-1 flex flex-wrap gap-1"
    >
      <CategoryChip
        value="all"
        label="All"
        active={current === "all"}
        onSelect={onSelect}
      />
      {CATEGORY_OPTIONS.map((option) => (
        <CategoryChip
          key={option.value}
          value={option.value}
          label={option.label}
          active={current === option.value}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

interface CategoryChipProps {
  value: VenueCommentCategory | "all";
  label: string;
  active: boolean;
  onSelect: (next: VenueCommentCategory | "all") => void;
}

function CategoryChip({
  value,
  label,
  active,
  onSelect,
}: CategoryChipProps): JSX.Element {
  return (
    <button
      role="tab"
      type="button"
      aria-selected={active}
      onClick={() => onSelect(value)}
      className={
        "rounded-full border px-3 py-1 text-xs font-medium transition " +
        (active
          ? "border-green-primary bg-green-primary text-text-inverse"
          : "border-border bg-bg-white text-text-secondary hover:border-green-primary hover:text-text-primary")
      }
    >
      {label}
    </button>
  );
}

interface CommentComposerProps {
  slug: string;
  token: string;
  defaultCategory: VenueCommentCategory;
  onPosted: () => void | Promise<void>;
}

function CommentComposer({
  slug,
  token,
  defaultCategory,
  onPosted,
}: CommentComposerProps): JSX.Element {
  const { show: showToast } = useToast();
  const [body, setBody] = useState<string>("");
  const [category, setCategory] =
    useState<VenueCommentCategory>(defaultCategory);
  // Honeypot — if a bot fills this out we silently fail the post.
  const [honeypot, setHoneypot] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  useEffect(() => {
    setCategory(defaultCategory);
  }, [defaultCategory]);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>): Promise<void> => {
      event.preventDefault();
      const trimmed = body.trim();
      if (trimmed.length < 2) {
        showToast("Comment is too short.");
        return;
      }
      if (trimmed.length > MAX_BODY_LEN) {
        showToast(`Comment exceeds the ${MAX_BODY_LEN}-character limit.`);
        return;
      }
      setIsSubmitting(true);
      try {
        await submitVenueComment(slug, token, {
          category,
          body: trimmed,
          honeypot,
        });
        setBody("");
        setHoneypot("");
        await onPosted();
      } catch (err) {
        const message =
          err instanceof ApiRequestError
            ? err.message
            : "Could not post comment.";
        showToast(message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [body, category, honeypot, slug, token, showToast, onPosted],
  );

  const remaining = MAX_BODY_LEN - body.length;

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-2 rounded-lg border border-border bg-bg-white p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor="new-comment-category"
          className="text-xs font-medium text-text-secondary"
        >
          Category
        </label>
        <select
          id="new-comment-category"
          value={category}
          onChange={(e: ChangeEvent<HTMLSelectElement>) =>
            setCategory(e.target.value as VenueCommentCategory)
          }
          className="rounded-md border border-border bg-bg-white px-2 py-1 text-sm"
        >
          {CATEGORY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <textarea
        aria-label="New comment"
        placeholder="Share a tip — parking, lines, best spot to stand, etc."
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        maxLength={MAX_BODY_LEN + 100}
        className="w-full resize-y rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary focus:border-green-primary focus:outline-none"
      />
      {/* Honeypot field — kept visually hidden but present in the DOM. */}
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
          disabled={isSubmitting || body.trim().length < 2}
          className="rounded-md bg-green-primary px-3 py-1.5 text-sm font-semibold text-text-inverse transition disabled:opacity-50 hover:bg-green-dark"
        >
          {isSubmitting ? "Posting…" : "Post tip"}
        </button>
      </div>
    </form>
  );
}

interface CommentItemProps {
  comment: VenueComment;
  onVote: (comment: VenueComment, value: -1 | 1) => Promise<void>;
}

function CommentItem({ comment, onVote }: CommentItemProps): JSX.Element {
  const net = comment.likes - comment.dislikes;
  return (
    <article className="flex gap-3 rounded-lg border border-border bg-bg-white p-3">
      <div className="flex w-10 flex-col items-center gap-1 text-xs text-text-secondary">
        <VoteButton
          direction="up"
          active={comment.viewer_vote === 1}
          onClick={() => onVote(comment, 1)}
        />
        <span
          aria-label={`${net} net votes`}
          className="font-semibold text-text-primary"
        >
          {net}
        </span>
        <VoteButton
          direction="down"
          active={comment.viewer_vote === -1}
          onClick={() => onVote(comment, -1)}
        />
      </div>
      <div className="flex flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
          <span className="inline-flex items-center rounded-full bg-bg-surface px-2 py-0.5 capitalize">
            {comment.category.replace("_", " ")}
          </span>
          {comment.created_at ? (
            <time dateTime={comment.created_at}>
              {formatTimestamp(comment.created_at)}
            </time>
          ) : null}
        </div>
        <p className="whitespace-pre-wrap text-sm text-text-primary">
          {comment.body}
        </p>
      </div>
    </article>
  );
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

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffMinutes = Math.floor(diffMs / 60_000);
  if (diffMinutes < 1) return "just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
  });
}

function applyOptimisticVote(
  comment: VenueComment,
  targetId: string,
  newValue: -1 | 0 | 1,
): VenueComment {
  if (comment.id !== targetId) return comment;
  const prev = comment.viewer_vote ?? 0;
  if (prev === newValue) return comment;
  let likes = comment.likes;
  let dislikes = comment.dislikes;
  if (prev === 1) likes -= 1;
  if (prev === -1) dislikes -= 1;
  if (newValue === 1) likes += 1;
  if (newValue === -1) dislikes += 1;
  const next: VenueComment["viewer_vote"] =
    newValue === 0 ? null : (newValue as -1 | 1);
  return { ...comment, likes, dislikes, viewer_vote: next };
}
