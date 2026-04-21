/**
 * Step 4 — Passkey: WebAuthn registration.
 *
 * Auto-completes for browsers that don't support WebAuthn — no point
 * blocking the finish line on something the device can't do.
 */

"use client";

import { useEffect, useState } from "react";

import {
  completePasskeyRegistration,
  startPasskeyRegistration,
} from "@/lib/api/auth-identity";
import {
  decodeRegistrationOptions,
  encodeRegistrationCredential,
  isWebAuthnSupported,
} from "@/lib/webauthn";

interface Props {
  token: string;
  onDone: () => void;
  onSkip: () => void;
}

export function PasskeyStep({ token, onDone, onSkip }: Props): JSX.Element {
  const [supported, setSupported] = useState<boolean>(true);
  const [status, setStatus] = useState<
    "idle" | "registering" | "done" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState<string>("");

  useEffect(() => {
    setSupported(isWebAuthnSupported());
  }, []);

  async function handleRegister(): Promise<void> {
    setStatus("registering");
    setError(null);
    try {
      const { options, state } = await startPasskeyRegistration(token);
      const credential = (await navigator.credentials.create({
        publicKey: decodeRegistrationOptions(options),
      })) as PublicKeyCredential | null;
      if (!credential) {
        throw new Error("Passkey creation was cancelled.");
      }
      await completePasskeyRegistration(
        token,
        encodeRegistrationCredential(credential),
        state,
        label.trim() || undefined,
      );
      setStatus("done");
      onDone();
    } catch (err) {
      setStatus("error");
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError("Passkey creation was cancelled.");
      } else {
        setError(
          err instanceof Error ? err.message : "Could not register a passkey.",
        );
      }
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-xl font-semibold text-text-primary">
          One-tap sign in with a passkey
        </h2>
        <p className="mt-1 text-sm text-text-secondary">
          Passkeys replace email links with a Face ID or Touch ID prompt next
          time. They live on your device and never leave it.
        </p>
      </header>

      {!supported ? (
        <div className="rounded-lg border border-border bg-bg-white p-4 text-sm text-text-secondary">
          This browser doesn&apos;t support passkeys. You can add one later
          from Settings once you&apos;re on a supported browser.
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-bg-white p-4">
          <label className="block">
            <span className="block text-xs font-medium uppercase tracking-wide text-text-secondary">
              Device label (optional)
            </span>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="MacBook Pro"
              className="mt-1 w-full rounded-md border border-border bg-bg-white px-3 py-2 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() => void handleRegister()}
            disabled={status === "registering"}
            className="mt-3 rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse disabled:cursor-not-allowed disabled:opacity-60"
          >
            {status === "registering"
              ? "Waiting for passkey…"
              : "Add a passkey"}
          </button>
        </div>
      )}

      {error ? (
        <p className="text-xs text-blush-accent" role="alert">
          {error}
        </p>
      ) : null}

      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onSkip}
          className="text-xs font-medium text-text-secondary underline underline-offset-2"
        >
          Skip for now
        </button>
        {!supported ? (
          <button
            type="button"
            onClick={onDone}
            className="rounded-md bg-green-primary px-4 py-2 text-sm font-medium text-text-inverse"
          >
            Continue
          </button>
        ) : null}
      </div>
    </div>
  );
}
