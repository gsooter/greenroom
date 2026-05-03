/**
 * Tests for the InstallPrompt banner.
 *
 * The banner has a non-trivial gating chain (PWA-not-installed AND
 * mobile-installable browser AND signed-in AND 60s dwell AND 2+ page
 * views AND not recently dismissed) and two platform-specific bodies
 * (Android with a programmatic prompt, iOS with manual instructions).
 * These tests pin each gate plus the two body variants.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import InstallPrompt from "@/components/pwa/InstallPrompt";

const isAppInstalled = vi.fn(() => false);
const isMobileBrowserInstallable = vi.fn(() => true);
const isMobileSafari = vi.fn(() => false);

vi.mock("@/lib/pwa-detection", () => ({
  isAppInstalled: () => isAppInstalled(),
  isMobileBrowserInstallable: () => isMobileBrowserInstallable(),
  isMobileSafari: () => isMobileSafari(),
}));

let mockAuth = { isAuthenticated: false };

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

let mockPathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

const DWELL_MS = 60 * 1000;

beforeEach(() => {
  isAppInstalled.mockReturnValue(false);
  isMobileBrowserInstallable.mockReturnValue(true);
  isMobileSafari.mockReturnValue(false);
  mockAuth = { isAuthenticated: true };
  mockPathname = "/";
  window.localStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

function bumpPageView(rerender: (ui: React.ReactElement) => void, path: string) {
  mockPathname = path;
  rerender(<InstallPrompt />);
}

function fireBeforeInstallPrompt(): { prompt: ReturnType<typeof vi.fn>; userChoice: Promise<{ outcome: "accepted"; platform: string }> } {
  const userChoice = Promise.resolve({ outcome: "accepted" as const, platform: "web" });
  const prompt = vi.fn().mockResolvedValue(undefined);
  const event = new Event("beforeinstallprompt") as Event & {
    prompt: typeof prompt;
    userChoice: typeof userChoice;
    platforms: readonly string[];
  };
  Object.defineProperty(event, "prompt", { value: prompt });
  Object.defineProperty(event, "userChoice", { value: userChoice });
  Object.defineProperty(event, "platforms", { value: ["web"] });
  window.dispatchEvent(event);
  return { prompt, userChoice };
}

describe("InstallPrompt", () => {
  it("renders nothing when the app is already installed", () => {
    isAppInstalled.mockReturnValue(true);
    const { container } = render(<InstallPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the browser cannot install a PWA", () => {
    isMobileBrowserInstallable.mockReturnValue(false);
    const { container } = render(<InstallPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the user is not signed in", () => {
    mockAuth = { isAuthenticated: false };
    const { container, rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("waits for the dwell timer before showing", () => {
    const { container, rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      vi.advanceTimersByTime(DWELL_MS - 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("requires at least two distinct page views", () => {
    const { container } = render(<InstallPrompt />);
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    // Only one page view counted (initial render); should still hide.
    expect(container.firstChild).toBeNull();
  });

  it("shows the iOS body with manual share-sheet instructions", () => {
    isMobileSafari.mockReturnValue(true);
    const { rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/Add to Home Screen/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add to home screen/i })).toBeNull();
  });

  it("shows the Android body with an Add button when beforeinstallprompt fires", () => {
    const { rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      fireBeforeInstallPrompt();
    });
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const addBtn = screen.getByRole("button", { name: /Add to home screen/i });
    expect(addBtn).toBeEnabled();
  });

  it("calls prompt() on the captured event when Add is clicked", async () => {
    const { rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    let prompt!: ReturnType<typeof vi.fn>;
    act(() => {
      ({ prompt } = fireBeforeInstallPrompt());
    });
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    const addBtn = screen.getByRole("button", { name: /Add to home screen/i });
    await act(async () => {
      fireEvent.click(addBtn);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(prompt).toHaveBeenCalled();
  });

  it("dismiss button writes a localStorage cooldown and hides the banner", () => {
    const { container, rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Dismiss install prompt/i }));
    expect(container.firstChild).toBeNull();
    expect(window.localStorage.getItem("greenroom_install_prompt_dismissed_at")).not.toBeNull();
  });

  it("respects an existing dismissal cooldown", () => {
    window.localStorage.setItem(
      "greenroom_install_prompt_dismissed_at",
      String(Date.now()),
    );
    const { container, rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(container.firstChild).toBeNull();
  });

  it("ignores an expired dismissal cooldown", () => {
    const eightDaysAgo = Date.now() - 8 * 24 * 60 * 60 * 1000;
    window.localStorage.setItem(
      "greenroom_install_prompt_dismissed_at",
      String(eightDaysAgo),
    );
    isMobileSafari.mockReturnValue(true);
    const { rerender } = render(<InstallPrompt />);
    bumpPageView(rerender, "/events");
    act(() => {
      vi.advanceTimersByTime(DWELL_MS + 100);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
