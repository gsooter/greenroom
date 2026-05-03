/**
 * Tests for the browser-side push subscribe pipeline.
 *
 * The pipeline has four moving parts (VAPID fetch, service-worker
 * registration, ``pushManager.subscribe``, backend POST) and a typed
 * error class that the UI uses to render specific guidance. These
 * tests pin each piece in isolation and the ``enablePush`` orchestrator
 * end-to-end with all four mocked.
 */

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import { ApiNotFoundError, ApiRequestError } from "./client";
import {
  PushUnavailableError,
  deleteSubscriptionFromBackend,
  enablePush,
  ensureServiceWorker,
  getVapidPublicKey,
  postSubscriptionToBackend,
  subscribeBrowserToPush,
} from "./push";

const fetchJson = vi.fn();

vi.mock("@/lib/api/client", async () => {
  const actual = await vi.importActual<typeof import("./client")>("./client");
  return {
    ...actual,
    fetchJson: (...args: unknown[]) => (fetchJson as unknown as Mock)(...args),
  };
});

vi.mock("@/lib/config", () => ({
  config: { apiUrl: "https://api.test", baseUrl: "https://app.test" },
}));

const realServiceWorker = navigator.serviceWorker;
const realNotification = (globalThis as { Notification?: unknown }).Notification;

function setNavigatorServiceWorker(value: unknown): void {
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value,
  });
}

function setNotification(value: unknown): void {
  Object.defineProperty(globalThis, "Notification", {
    configurable: true,
    writable: true,
    value,
  });
}

beforeEach(() => {
  fetchJson.mockReset();
});

afterEach(() => {
  setNavigatorServiceWorker(realServiceWorker);
  setNotification(realNotification);
});

describe("PushUnavailableError", () => {
  it("carries a reason tag the UI can branch on without parsing", () => {
    const err = new PushUnavailableError("not_configured", "msg");
    expect(err.reason).toBe("not_configured");
    expect(err.name).toBe("PushUnavailableError");
    expect(err.message).toBe("msg");
    expect(err).toBeInstanceOf(Error);
  });
});

describe("getVapidPublicKey", () => {
  it("returns the public key on success", async () => {
    fetchJson.mockResolvedValueOnce({ data: { public_key: "BCp" } });
    await expect(getVapidPublicKey()).resolves.toBe("BCp");
    expect(fetchJson).toHaveBeenCalledWith(
      "/api/v1/push/vapid-public-key",
      expect.objectContaining({ revalidateSeconds: 0 }),
    );
  });

  it("maps a 404 to PushUnavailableError(endpoint_missing)", async () => {
    fetchJson.mockRejectedValueOnce(
      new ApiNotFoundError("HTTP_ERROR", "404 NOT FOUND"),
    );
    await expect(getVapidPublicKey()).rejects.toMatchObject({
      name: "PushUnavailableError",
      reason: "endpoint_missing",
    });
  });

  it("maps a 500 to PushUnavailableError(server_error)", async () => {
    fetchJson.mockRejectedValueOnce(
      new ApiRequestError(500, "HTTP_ERROR", "boom"),
    );
    await expect(getVapidPublicKey()).rejects.toMatchObject({
      name: "PushUnavailableError",
      reason: "server_error",
    });
  });

  it("re-throws unrecognized errors verbatim", async () => {
    fetchJson.mockRejectedValueOnce(new Error("network down"));
    await expect(getVapidPublicKey()).rejects.toThrow("network down");
  });
});

describe("ensureServiceWorker", () => {
  it("throws when the browser lacks serviceWorker", async () => {
    setNavigatorServiceWorker(undefined);
    await expect(ensureServiceWorker()).rejects.toThrow(/not supported/i);
  });

  it("returns the existing registration when one is present", async () => {
    const target = { postMessage: vi.fn() };
    const existing = {
      active: target,
      waiting: null,
      installing: null,
    } as unknown as ServiceWorkerRegistration;
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue(existing),
      register: vi.fn(),
    });
    await expect(ensureServiceWorker()).resolves.toBe(existing);
    expect(target.postMessage).toHaveBeenCalledWith({
      type: "set-api-base",
      apiBase: "https://api.test",
    });
  });

  it("registers a new worker when no existing registration", async () => {
    const target = { postMessage: vi.fn() };
    const fresh = {
      active: target,
      waiting: null,
      installing: null,
    } as unknown as ServiceWorkerRegistration;
    const register = vi.fn().mockResolvedValue(fresh);
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue(undefined),
      register,
    });
    await expect(ensureServiceWorker()).resolves.toBe(fresh);
    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
    expect(target.postMessage).toHaveBeenCalled();
  });

  it("falls back to waiting then installing when active is missing", async () => {
    const installing = { postMessage: vi.fn() };
    const reg = {
      active: null,
      waiting: null,
      installing,
    } as unknown as ServiceWorkerRegistration;
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue(reg),
      register: vi.fn(),
    });
    await ensureServiceWorker();
    expect(installing.postMessage).toHaveBeenCalled();
  });
});

describe("subscribeBrowserToPush", () => {
  it("returns the existing subscription when one is present", async () => {
    const existing = { endpoint: "https://push/abc" };
    const reg = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(existing),
        subscribe: vi.fn(),
      },
    } as unknown as ServiceWorkerRegistration;
    await expect(
      subscribeBrowserToPush(reg, "BCp"),
    ).resolves.toBe(existing as unknown as PushSubscription);
    expect(reg.pushManager.subscribe).not.toHaveBeenCalled();
  });

  it("subscribes with a copied ArrayBuffer when none exists", async () => {
    const subscribed = { endpoint: "https://push/new" };
    const subscribe = vi.fn().mockResolvedValue(subscribed);
    const reg = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe,
      },
    } as unknown as ServiceWorkerRegistration;
    await subscribeBrowserToPush(reg, "BCp");
    const call = subscribe.mock.calls[0]?.[0];
    expect(call?.userVisibleOnly).toBe(true);
    expect(call?.applicationServerKey).toBeInstanceOf(ArrayBuffer);
  });

  it("strips trailing whitespace from a key pasted with a newline", async () => {
    const subscribe = vi.fn().mockResolvedValue({ endpoint: "https://push/x" });
    const reg = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe,
      },
    } as unknown as ServiceWorkerRegistration;
    await expect(subscribeBrowserToPush(reg, "BCp\n")).resolves.toBeTruthy();
  });

  it("throws a clear error pointing at VAPID_PUBLIC_KEY when the key has invalid characters", async () => {
    const reg = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(null),
        subscribe: vi.fn(),
      },
    } as unknown as ServiceWorkerRegistration;
    // "@" and "!" never appear in any base64 variant; the regex must
    // reject before atob throws its less-helpful InvalidCharacterError.
    await expect(
      subscribeBrowserToPush(reg, "BCp@!#"),
    ).rejects.toThrow(/VAPID_PUBLIC_KEY/);
  });
});

describe("postSubscriptionToBackend", () => {
  it("POSTs the subscription JSON with the bearer token", async () => {
    fetchJson.mockResolvedValueOnce({ data: { subscribed: true } });
    const sub = {
      toJSON: () => ({ endpoint: "https://push/abc", keys: { p256dh: "p", auth: "a" } }),
    } as unknown as PushSubscription;
    await postSubscriptionToBackend(sub, "tok-1");
    expect(fetchJson).toHaveBeenCalledWith(
      "/api/v1/push/subscribe",
      expect.objectContaining({
        method: "POST",
        body: { endpoint: "https://push/abc", keys: { p256dh: "p", auth: "a" } },
        token: "tok-1",
      }),
    );
  });
});

describe("deleteSubscriptionFromBackend", () => {
  it("DELETEs by endpoint and surfaces the removed flag", async () => {
    fetchJson.mockResolvedValueOnce({ data: { removed: true } });
    await expect(
      deleteSubscriptionFromBackend("https://push/abc", "tok-2"),
    ).resolves.toBe(true);
    expect(fetchJson).toHaveBeenCalledWith(
      "/api/v1/push/subscriptions",
      expect.objectContaining({
        method: "DELETE",
        body: { endpoint: "https://push/abc" },
        token: "tok-2",
      }),
    );
  });
});

describe("enablePush", () => {
  function mockSuccessfulPipeline(): {
    requestPermission: Mock;
    subscribe: Mock;
  } {
    fetchJson.mockImplementation((path: string) => {
      if (path === "/api/v1/push/vapid-public-key") {
        return Promise.resolve({ data: { public_key: "BCp" } });
      }
      if (path === "/api/v1/push/subscribe") {
        return Promise.resolve({ data: { subscribed: true } });
      }
      return Promise.reject(new Error(`unexpected ${path}`));
    });
    const requestPermission = vi.fn().mockResolvedValue("granted");
    setNotification(
      Object.assign(function MockNotification() {}, {
        permission: "default",
        requestPermission,
      }),
    );
    const sub = {
      endpoint: "https://push/abc",
      toJSON: () => ({ endpoint: "https://push/abc", keys: { p256dh: "p", auth: "a" } }),
    };
    const subscribe = vi.fn().mockResolvedValue(sub);
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue({
        active: { postMessage: vi.fn() },
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue(null),
          subscribe,
        },
      }),
      register: vi.fn(),
    });
    return { requestPermission, subscribe };
  }

  it("walks the four-step pipeline on the happy path", async () => {
    const { requestPermission, subscribe } = mockSuccessfulPipeline();
    await enablePush("tok-1");
    expect(requestPermission).toHaveBeenCalled();
    expect(subscribe).toHaveBeenCalled();
    expect(fetchJson).toHaveBeenCalledWith(
      "/api/v1/push/subscribe",
      expect.objectContaining({ token: "tok-1" }),
    );
  });

  it("throws not_configured when the backend returns an empty key", async () => {
    fetchJson.mockResolvedValueOnce({ data: { public_key: "" } });
    await expect(enablePush("tok-1")).rejects.toMatchObject({
      reason: "not_configured",
    });
  });

  it("throws browser_unsupported when Notification API is missing", async () => {
    fetchJson.mockResolvedValueOnce({ data: { public_key: "BCp" } });
    setNotification(undefined);
    await expect(enablePush("tok-1")).rejects.toMatchObject({
      reason: "browser_unsupported",
    });
  });

  it("throws permission_denied when the user blocks the prompt", async () => {
    fetchJson.mockResolvedValueOnce({ data: { public_key: "BCp" } });
    setNotification(
      Object.assign(function MockNotification() {}, {
        permission: "default",
        requestPermission: vi.fn().mockResolvedValue("denied"),
      }),
    );
    await expect(enablePush("tok-1")).rejects.toMatchObject({
      reason: "permission_denied",
    });
  });

  it("wraps a pushManager.subscribe failure in subscribe_failed with the underlying message", async () => {
    fetchJson.mockResolvedValueOnce({ data: { public_key: "BCp" } });
    setNotification(
      Object.assign(function MockNotification() {}, {
        permission: "granted",
        requestPermission: vi.fn(),
      }),
    );
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue({
        active: { postMessage: vi.fn() },
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue(null),
          subscribe: vi.fn().mockRejectedValue(
            new Error("AbortError: Registration failed - push service error"),
          ),
        },
      }),
      register: vi.fn(),
    });
    await expect(enablePush("tok-1")).rejects.toMatchObject({
      reason: "subscribe_failed",
      message: expect.stringContaining("push service error"),
    });
  });

  it("wraps a backend POST failure in subscribe_post_failed with the underlying message", async () => {
    fetchJson.mockImplementation((path: string) => {
      if (path === "/api/v1/push/vapid-public-key") {
        return Promise.resolve({ data: { public_key: "BCp" } });
      }
      return Promise.reject(new Error("422 unprocessable entity"));
    });
    setNotification(
      Object.assign(function MockNotification() {}, {
        permission: "granted",
        requestPermission: vi.fn(),
      }),
    );
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue({
        active: { postMessage: vi.fn() },
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue({
            endpoint: "https://push/abc",
            toJSON: () => ({ endpoint: "https://push/abc", keys: { p256dh: "p", auth: "a" } }),
          }),
          subscribe: vi.fn(),
        },
      }),
      register: vi.fn(),
    });
    await expect(enablePush("tok-1")).rejects.toMatchObject({
      reason: "subscribe_post_failed",
      message: expect.stringContaining("422"),
    });
  });

  it("re-uses an already-granted permission without re-prompting", async () => {
    const requestPermission = vi.fn();
    fetchJson.mockImplementation((path: string) => {
      if (path === "/api/v1/push/vapid-public-key") {
        return Promise.resolve({ data: { public_key: "BCp" } });
      }
      return Promise.resolve({ data: { subscribed: true } });
    });
    setNotification(
      Object.assign(function MockNotification() {}, {
        permission: "granted",
        requestPermission,
      }),
    );
    setNavigatorServiceWorker({
      getRegistration: vi.fn().mockResolvedValue({
        active: { postMessage: vi.fn() },
        pushManager: {
          getSubscription: vi.fn().mockResolvedValue({
            endpoint: "https://push/abc",
            toJSON: () => ({ endpoint: "https://push/abc", keys: { p256dh: "p", auth: "a" } }),
          }),
          subscribe: vi.fn(),
        },
      }),
      register: vi.fn(),
    });
    await enablePush("tok-1");
    expect(requestPermission).not.toHaveBeenCalled();
  });
});
