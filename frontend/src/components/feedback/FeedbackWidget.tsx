/**
 * Persistent "We're still building" feedback widget.
 *
 * Renders a small floating pill anchored to the bottom-right of every
 * page. Clicking it opens a modal with a textarea and a kind toggle
 * (bug / feature / general). Submitting posts to `/api/v1/feedback`.
 *
 * Auth-aware: when the user is signed in, the email is auto-filled
 * from the AuthContext and the field is hidden. Anonymous users see
 * an optional email input so we can reply.
 */

"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { submitFeedback, type FeedbackKind } from "@/lib/api/feedback";
import { useAuth } from "@/lib/auth";

const MAX_MESSAGE_LENGTH = 4000;
const DISMISSED_KEY = "greenroom.feedback.dismissed";

type Status = "idle" | "submitting" | "success" | "error";

interface KindOption {
  value: FeedbackKind;
  label: string;
  emoji: string;
}

const KIND_OPTIONS: KindOption[] = [
  { value: "bug", label: "Bug", emoji: "🐞" },
  { value: "feature", label: "Feature", emoji: "✨" },
  { value: "general", label: "General", emoji: "💬" },
];

export default function FeedbackWidget(): JSX.Element | null {
  const { user, token } = useAuth();
  const [open, setOpen] = useState(false);
  const [pillVisible, setPillVisible] = useState(true);
  const [kind, setKind] = useState<FeedbackKind>("general");
  const [message, setMessage] = useState("");
  const [emailInput, setEmailInput] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Restore the per-session dismiss preference. The pill is dismissible
  // for the current browser session only — sessionStorage means it
  // comes back on the next visit so we don't lose ongoing feedback.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.sessionStorage.getItem(DISMISSED_KEY) === "1") {
      setPillVisible(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const timer = window.setTimeout(() => textareaRef.current?.focus(), 50);
    return () => {
      document.body.style.overflow = previous;
      window.clearTimeout(timer);
    };
  }, [open]);

  const isAuthed = Boolean(user);
  const remaining = MAX_MESSAGE_LENGTH - message.length;
  const canSubmit = useMemo(
    () => message.trim().length > 0 && status !== "submitting",
    [message, status],
  );

  function dismissPill(): void {
    setPillVisible(false);
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem(DISMISSED_KEY, "1");
    }
  }

  function resetForm(): void {
    setMessage("");
    setEmailInput("");
    setKind("general");
    setStatus("idle");
    setErrorMessage(null);
  }

  function closeModal(): void {
    setOpen(false);
    if (status === "success") resetForm();
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    setStatus("submitting");
    setErrorMessage(null);
    try {
      const pageUrl =
        typeof window !== "undefined" ? window.location.href : null;
      await submitFeedback(
        {
          message: message.trim(),
          kind,
          email: isAuthed ? null : emailInput.trim() || null,
          page_url: pageUrl,
        },
        token,
      );
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setErrorMessage(
        err instanceof Error
          ? err.message
          : "Something went wrong. Please try again.",
      );
    }
  }

  return (
    <>
      {pillVisible && !open ? (
        <div className="pointer-events-none fixed bottom-24 right-4 z-40 flex items-end gap-1 sm:bottom-6 sm:right-6">
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="pointer-events-auto inline-flex items-center gap-2 rounded-full bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse shadow-lg transition hover:bg-green-dark focus:outline-none focus-visible:ring-2 focus-visible:ring-green-dark focus-visible:ring-offset-2"
            aria-label="Leave feedback"
            data-testid="feedback-pill"
          >
            <span aria-hidden="true">💬</span>
            <span>Greenroom&apos;s in beta — feedback?</span>
          </button>
          <button
            type="button"
            onClick={dismissPill}
            className="pointer-events-auto rounded-full bg-bg-white/90 px-2 py-1 text-xs text-text-secondary shadow transition hover:bg-bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-green-primary"
            aria-label="Dismiss feedback prompt"
          >
            ✕
          </button>
        </div>
      ) : null}

      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-text-primary/40 px-4 pb-6 pt-20 sm:items-center sm:pb-0"
          role="dialog"
          aria-modal="true"
          aria-labelledby="feedback-modal-title"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeModal();
          }}
        >
          <div className="w-full max-w-md rounded-2xl bg-bg-white p-5 shadow-xl">
            {status === "success" ? (
              <div className="space-y-4 text-text-primary">
                <h2
                  id="feedback-modal-title"
                  className="text-lg font-semibold"
                >
                  Thanks — we got it.
                </h2>
                <p className="text-sm text-text-secondary">
                  We read every note. If you left an email we&apos;ll
                  follow up when we ship something related.
                </p>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={closeModal}
                    className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse hover:bg-green-dark"
                  >
                    Close
                  </button>
                </div>
              </div>
            ) : (
              <form className="space-y-4" onSubmit={handleSubmit} noValidate>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2
                      id="feedback-modal-title"
                      className="text-lg font-semibold text-text-primary"
                    >
                      We&apos;re still building.
                    </h2>
                    <p className="mt-1 text-sm text-text-secondary">
                      Tell us what&apos;s broken or what you wish
                      Greenroom could do.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={closeModal}
                    className="rounded-full p-1 text-text-secondary hover:bg-bg-surface"
                    aria-label="Close feedback form"
                  >
                    ✕
                  </button>
                </div>

                <fieldset className="space-y-2">
                  <legend className="text-xs font-medium uppercase tracking-wide text-text-secondary">
                    Type
                  </legend>
                  <div className="flex gap-2">
                    {KIND_OPTIONS.map((option) => {
                      const isActive = option.value === kind;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() => setKind(option.value)}
                          aria-pressed={isActive}
                          className={`flex-1 rounded-md border px-3 py-2 text-sm transition ${
                            isActive
                              ? "border-green-primary bg-green-soft text-text-primary"
                              : "border-border bg-bg-surface text-text-secondary hover:border-green-primary/60"
                          }`}
                        >
                          <span className="mr-1" aria-hidden="true">
                            {option.emoji}
                          </span>
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                </fieldset>

                <div className="space-y-1">
                  <label
                    htmlFor="feedback-message"
                    className="text-xs font-medium uppercase tracking-wide text-text-secondary"
                  >
                    What&apos;s on your mind?
                  </label>
                  <textarea
                    id="feedback-message"
                    ref={textareaRef}
                    rows={5}
                    value={message}
                    maxLength={MAX_MESSAGE_LENGTH}
                    onChange={(event) => setMessage(event.target.value)}
                    placeholder="A bug you hit, a feature you'd want, anything..."
                    className="w-full rounded-md border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:border-green-primary focus:outline-none focus:ring-2 focus:ring-green-primary/30"
                    required
                  />
                  <p className="text-right text-xs text-text-secondary">
                    {remaining.toLocaleString()} characters left
                  </p>
                </div>

                {!isAuthed ? (
                  <div className="space-y-1">
                    <label
                      htmlFor="feedback-email"
                      className="text-xs font-medium uppercase tracking-wide text-text-secondary"
                    >
                      Email (optional)
                    </label>
                    <input
                      id="feedback-email"
                      type="email"
                      value={emailInput}
                      onChange={(event) => setEmailInput(event.target.value)}
                      placeholder="you@example.com"
                      className="w-full rounded-md border border-border bg-bg-base px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:border-green-primary focus:outline-none focus:ring-2 focus:ring-green-primary/30"
                    />
                  </div>
                ) : (
                  <p className="text-xs text-text-secondary">
                    We&apos;ll reply to{" "}
                    <span className="font-medium text-text-primary">
                      {user?.email}
                    </span>{" "}
                    if needed.
                  </p>
                )}

                {status === "error" && errorMessage ? (
                  <p
                    className="rounded-md bg-blush-soft px-3 py-2 text-sm text-blush-accent"
                    role="alert"
                  >
                    {errorMessage}
                  </p>
                ) : null}

                <div className="flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={closeModal}
                    className="rounded-md px-4 py-2 text-sm text-text-secondary hover:text-text-primary"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse hover:bg-green-dark disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {status === "submitting" ? "Sending…" : "Send feedback"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}
