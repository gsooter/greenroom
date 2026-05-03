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

import { fetchJson } from "@/lib/api/client";

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
  const response = await fetchJson<VapidKeyResponse>(
    "/api/v1/push/vapid-public-key",
    { revalidateSeconds: 0 },
  );
  return response.data.public_key;
}

export async function ensureServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!("serviceWorker" in navigator)) {
    throw new Error("Service workers are not supported in this browser.");
  }
  const existing = await navigator.serviceWorker.getRegistration("/");
  if (existing) return existing;
  return navigator.serviceWorker.register(SERVICE_WORKER_URL, {
    scope: "/",
  });
}

export async function subscribeBrowserToPush(
  registration: ServiceWorkerRegistration,
  publicKey: string,
): Promise<PushSubscription> {
  const existing = await registration.pushManager.getSubscription();
  if (existing) return existing;
  return registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
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
    throw new Error("Push is not configured on the server.");
  }
  if (!("Notification" in window)) {
    throw new Error("This browser does not support notifications.");
  }
  const permission =
    Notification.permission === "default"
      ? await Notification.requestPermission()
      : Notification.permission;
  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }
  const registration = await ensureServiceWorker();
  const subscription = await subscribeBrowserToPush(registration, publicKey);
  await postSubscriptionToBackend(subscription, token);
  return subscription;
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
  return output;
}
