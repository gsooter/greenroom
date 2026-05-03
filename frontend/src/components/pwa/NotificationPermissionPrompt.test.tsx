/**
 * Tests for the NotificationPermissionPrompt banner.
 *
 * The banner gates on PWA-installed AND signed-in AND default permission
 * AND no recent dismissal AND a 30s session dwell. Once visible, it
 * triggers the four-step push subscribe pipeline (mocked here) and maps
 * a typed PushUnavailableError onto a friendlier UI message.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NotificationPermissionPrompt from "@/components/pwa/NotificationPermissionPrompt";
import { PushUnavailableError } from "@/lib/api/push";

const enablePush = vi.fn();

vi.mock("@/lib/api/push", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/push")>(
    "@/lib/api/push",
  );
  return {
    ...actual,
    enablePush: (...args: unknown[]) => enablePush(...args),
  };
});

const isAppInstalled = vi.fn(() => true);

vi.mock("@/lib/pwa-detection", () => ({
  isAppInstalled: () => isAppInstalled(),
}));

let mockAuth: { isAuthenticated: boolean; token: string | null } = {
  isAuthenticated: true,
  token: "tok-1",
};

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

const DWELL_MS = 30 * 1000;
const realNotification = (globalThis as { Notification?: unknown }).Notification;

function setNotification(value: unknown): void {
  Object.defineProperty(globalThis, "Notification", {
    configurable: true,
    writable: true,
    value,
  });
}

beforeEach(() => {
  enablePush.mockReset();
  isAppInstalled.mockReturnValue(true);
  mockAuth = { isAuthenticated: true, token: "tok-1" };
  window.localStorage.clear();
  setNotification(
    Object.assign(function MockNotification() {}, {
      permission: "default",
      requestPermission: vi.fn().mockResolvedValue("granted"),
    }),
  );
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  setNotification(realNotification);
});

describe("NotificationPermissionPrompt", () => {
  it("renders nothing when the app is not installed (PWA-only gate)", () => {
    isAppInstalled.mockReturnValue(false);
    const { container } = render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the user is not signed in", () => {
    mockAuth = { isAuthenticated: false, token: null };
    const { container } = render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when push has already been enabled in localStorage", () => {
    window.localStorage.setItem("greenroom_push_enabled", "1");
    const { container } = render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when a recent dismissal cooldown is in effect", () => {
    window.localStorage.setItem(
      "greenroom_push_prompt_dismissed_at",
      String(Date.now()),
    );
    const { container } = render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when permission is already granted or denied", () => {
    setNotification(
      Object.assign(function MockNotification() {}, {
        permission: "denied",
        requestPermission: vi.fn(),
      }),
    );
    const { container } = render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("waits for the 30s dwell before showing", () => {
    const { container } = render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS - 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("shows the prompt after the dwell timer fires on an eligible session", () => {
    render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Enable$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Not now$/i }),
    ).toBeInTheDocument();
  });

  it("clicking Enable calls enablePush, sets the localStorage flag, hides the prompt", async () => {
    enablePush.mockResolvedValue({ endpoint: "https://push/abc" });
    render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    fireEvent.click(screen.getByRole("button", { name: /^Enable$/i }));
    await waitFor(() => {
      expect(enablePush).toHaveBeenCalledWith("tok-1");
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
    expect(window.localStorage.getItem("greenroom_push_enabled")).toBe("1");
  });

  it("renders the PushUnavailableError message verbatim when enable fails", async () => {
    enablePush.mockRejectedValue(
      new PushUnavailableError("permission_denied", "User said no."),
    );
    render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    fireEvent.click(screen.getByRole("button", { name: /^Enable$/i }));
    await waitFor(() => {
      expect(screen.getByText(/User said no\./i)).toBeInTheDocument();
    });
    // Prompt stays visible so the user can retry or dismiss.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("falls back to a generic message for unknown errors", async () => {
    enablePush.mockRejectedValue(new Error("network exploded"));
    render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    fireEvent.click(screen.getByRole("button", { name: /^Enable$/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Could not enable notifications/i),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/network exploded/i)).toBeNull();
  });

  it("dismiss writes the cooldown and hides the banner", () => {
    render(<NotificationPermissionPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    fireEvent.click(screen.getByRole("button", { name: /^Not now$/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      window.localStorage.getItem("greenroom_push_prompt_dismissed_at"),
    ).not.toBeNull();
  });
});
