/**
 * Browser → backend push subscription helpers.
 *
 * The browser-side handshake to enable push has four steps:
 *
 *   1. Fetch the VAPID public key from the backend.
 *   2. Register the service worker (idempotent — registering an already-
 *      registered scope returns the existing registration).
 *   3. Ask the user for notification permission.
 *   4. Hand the public key to `pushManager.subscribe`, then POST the
 *      resulting subscription back to the backend so it can send to
 *      this browser.
 *
 * Each step has its own helper so callers can wire them into UI
 * states (loading the key, asking for permission, recording the
 * subscription) without re-implementing the orchestration. The
 * end-to-end flow is exposed as :func:`enablePush` for the common
 * "user clicked Enable" case.
 */

import { ApiNotFoundError, ApiRequestError, fetchJson } from "@/lib/api/client";
import { config } from "@/lib/config";

/**
 * User-facing error raised when the push pipeline isn't reachable or
 * isn't configured. Carries a ``reason`` tag so the prompt can render
 * a per-cause hint without parsing message strings.
 */
export class PushUnavailableError extends Error {
  readonly reason:
    | "endpoint_missing"
    | "server_error"
    | "not_configured"
    | "permission_denied"
    | "browser_unsupported";

  constructor(
    reason: PushUnavailableError["reason"],
    message: string,
  ) {
    super(message);
    this.name = "PushUnavailableError";
    this.reason = reason;
  }
}

interface VapidKeyResponse {
  data: { public_key: string };
}

interface SubscribeResponse {
  data: { subscribed: boolean };
}

interface UnsubscribeResponse {
  data: { removed: boolean };
}

const SERVICE_WORKER_URL = "/sw.js";

export async function getVapidPublicKey(): Promise<string> {
  try {
    const response = await fetchJson<VapidKeyResponse>(
      "/api/v1/push/vapid-public-key",
      { revalidateSeconds: 0 },
    );
    return response.data.public_key;
  } catch (err) {
    // The most common failure mode in real deployments is "the
    // backend hasn't been redeployed with the push routes yet" —
    // that surfaces as a 404 (Flask's default page when the JSON
    // parse falls through inside fetchJson). Re-raise as a typed
    // PushUnavailableError so the prompt can show a friendly
    // message instead of "404 NOT FOUND".
    if (err instanceof ApiNotFoundError) {
      throw new PushUnavailableError(
        "endpoint_missing",
        "Push notifications aren't ready on this server yet. Try again in a few minutes.",
      );
    }
    if (err instanceof ApiRequestError) {
      throw new PushUnavailableError(
        "server_error",
        "Push notifications hit a server error. Try again later.",
      );
    }
    throw err;
  }
}

export async function ensureServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker) {
    throw new Error("Service workers are not supported in this browser.");
  }
  const existing = await navigator.serviceWorker.getRegistration("/");
  const registration =
    existing ??
    (await navigator.serviceWorker.register(SERVICE_WORKER_URL, {
      scope: "/",
    }));
  // Hand the backend's absolute base URL to the worker so its
  // ``pushsubscriptionchange`` handler can re-fetch the VAPID key
  // and POST a refreshed subscription, even when the backend lives
  // on a different origin from the frontend (the common production
  // case). Sent every call because a worker may have re-installed
  // since the page last set this.
  const target = registration.active ?? registration.waiting ?? registration.installing;
  if (target) {
    target.postMessage({ type: "set-api-base", apiBase: config.apiUrl });
  }
  return registration;
}

export async function subscribeBrowserToPush(
  registration: ServiceWorkerRegistration,
  publicKey: string,
): Promise<PushSubscription> {
  const existing = await registration.pushManager.getSubscription();
  if (existing) return existing;
  // Copy into a fresh ArrayBuffer to satisfy TS's BufferSource type
  // — the lib.dom typing rejects Uint8Array<ArrayBufferLike> and
  // also won't accept a SharedArrayBuffer slice.
  const keyBytes = urlBase64ToUint8Array(publicKey);
  const keyBuffer = new ArrayBuffer(keyBytes.byteLength);
  new Uint8Array(keyBuffer).set(keyBytes);
  return registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBuffer,
  });
}

export async function postSubscriptionToBackend(
  subscription: PushSubscription,
  token: string,
): Promise<void> {
  await fetchJson<SubscribeResponse>("/api/v1/push/subscribe", {
    method: "POST",
    body: subscription.toJSON(),
    token,
  });
}

export async function deleteSubscriptionFromBackend(
  endpoint: string,
  token: string,
): Promise<boolean> {
  const response = await fetchJson<UnsubscribeResponse>(
    "/api/v1/push/subscriptions",
    {
      method: "DELETE",
      body: { endpoint },
      token,
    },
  );
  return response.data.removed;
}

/**
 * One-shot helper for the "user clicked Enable" path.
 *
 * Throws on any failure rather than swallowing — the calling
 * component is expected to surface the error to the user, since
 * permission prompts are user-driven and silent failure is the
 * worst possible UX.
 */
export async function enablePush(token: string): Promise<PushSubscription> {
  const publicKey = await getVapidPublicKey();
  if (!publicKey) {
    throw new PushUnavailableError(
      "not_configured",
      "Push notifications aren't configured for this environment yet.",
    );
  }
  const NotificationApi = (
    globalThis as { Notification?: typeof Notification }
  ).Notification;
  if (!NotificationApi) {
    throw new PushUnavailableError(
      "browser_unsupported",
      "This browser doesn't support push notifications.",
    );
  }
  const permission =
    NotificationApi.permission === "default"
      ? await NotificationApi.requestPermission()
      : NotificationApi.permission;
  if (permission !== "granted") {
    throw new PushUnavailableError(
      "permission_denied",
      "Notification permission wasn't granted. Enable it in your browser settings to try again.",
    );
  }
  const registration = await ensureServiceWorker();
  const subscription = await subscribeBrowserToPush(registration, publicKey);
  await postSubscriptionToBackend(subscription, token);
  return subscription;
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = globalThis.atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
  return output;
}
