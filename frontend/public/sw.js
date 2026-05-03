/* Greenroom service worker — push notification delivery only.
 *
 * This worker is intentionally minimal: it does not cache, does not
 * intercept fetches, and does not orchestrate background sync. It
 * exists so the browser has somewhere to deliver `push` events and
 * somewhere to dispatch `notificationclick` events to. Caching and
 * offline support can be layered on later without rewriting this
 * file — the install/activate hooks below already claim clients.
 *
 * The `push` handler tolerates malformed payloads: if the JSON parse
 * fails we still call `showNotification` with a generic body so the
 * user gets a ping instead of silent dead air.
 *
 * The page sends the absolute backend base URL via postMessage right
 * after registration (`{type: "set-api-base", apiBase: "..."}`). The
 * worker stores it and uses it inside `pushsubscriptionchange` to
 * re-fetch the VAPID key and re-POST the subscription. Without this
 * handshake the worker would resolve `/api/v1/...` against its own
 * scope (the frontend origin), which 404s in the common production
 * case where the backend lives on a different origin.
 */

let API_BASE = "";

self.addEventListener("install", (event) => {
  // Activate as soon as the new worker is installed so a refresh
  // doesn't leave the user on an old version.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("message", (event) => {
  const data = event.data;
  if (data && data.type === "set-api-base" && typeof data.apiBase === "string") {
    API_BASE = data.apiBase.replace(/\/$/, "");
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    if (event.data) {
      payload = event.data.json();
    }
  } catch (_err) {
    payload = { title: "Greenroom", body: "" };
  }

  const title = payload.title || "Greenroom";
  const options = {
    body: payload.body || "",
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: {
      url: payload.url || "/",
    },
    tag: payload.tag || undefined,
    renotify: !!payload.tag,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl =
    (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    (async () => {
      const clientList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // Reuse an existing tab if one is already open on Greenroom —
      // saves the user from juggling duplicate tabs.
      for (const client of clientList) {
        try {
          const url = new URL(client.url);
          if (url.origin === self.location.origin) {
            await client.focus();
            await client.navigate(targetUrl);
            return;
          }
        } catch (_err) {
          // ignore non-http clients
        }
      }
      await self.clients.openWindow(targetUrl);
    })(),
  );
});

self.addEventListener("pushsubscriptionchange", (event) => {
  // The browser rotated the subscription's keys (or the push service
  // expired the endpoint). Re-subscribe with the same VAPID public
  // key, then POST the new subscription back to the server. We swallow
  // failures here because the user will see no error UI either way —
  // the worst case is the next visit re-subscribes via the normal
  // permission flow.
  event.waitUntil(
    (async () => {
      if (!API_BASE) {
        // The page hasn't told us where the backend lives yet. Skip
        // — the next visit's enablePush() call will recreate the
        // subscription via the normal flow.
        return;
      }
      try {
        const response = await fetch(`${API_BASE}/api/v1/push/vapid-public-key`);
        const json = await response.json();
        const key = json && json.data && json.data.public_key;
        if (!key) return;
        const newSub = await self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(key),
        });
        await fetch(`${API_BASE}/api/v1/push/subscribe`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(newSub.toJSON()),
        });
      } catch (_err) {
        // best-effort
      }
    })(),
  );
});

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = self.atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; ++i) output[i] = raw.charCodeAt(i);
  return output;
}
