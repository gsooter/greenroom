/**
 * Tests for OnboardingBanner.
 *
 * The banner has three distinct responsibilities — (a) render only
 * when the server says it should, (b) bump the browse-session counter
 * once per browser session so the seven-session auto-hide eventually
 * fires, and (c) dismiss optimistically so the user doesn't see the
 * nudge again while the DELETE is in-flight. Each test isolates one.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OnboardingBanner } from "@/components/onboarding/OnboardingBanner";
import type { OnboardingState } from "@/types";

const getOnboardingState = vi.fn();
const incrementBrowseSessions = vi.fn();
const dismissOnboardingBanner = vi.fn();

let mockAuth: {
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
} = {
  token: "jwt",
  isAuthenticated: true,
  isLoading: false,
};

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

vi.mock("@/lib/api/onboarding", () => ({
  getOnboardingState: (token: string) => getOnboardingState(token),
  incrementBrowseSessions: (token: string) => incrementBrowseSessions(token),
  dismissOnboardingBanner: (token: string) => dismissOnboardingBanner(token),
}));

vi.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  } & Record<string, unknown>) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function makeState(overrides: Partial<OnboardingState> = {}): OnboardingState {
  return {
    steps: {
      taste: true,
      venues: true,
      music_services: true,
      passkey: true,
    },
    completed: true,
    skipped_entirely_at: "2026-04-20T00:00:00+00:00",
    banner: {
      visible: true,
      dismissed_at: null,
      browse_sessions_since_skipped: 2,
    },
    ...overrides,
  };
}

describe("OnboardingBanner", () => {
  beforeEach(() => {
    getOnboardingState.mockReset();
    incrementBrowseSessions.mockReset();
    dismissOnboardingBanner.mockReset();
    window.sessionStorage.clear();
    mockAuth = { token: "jwt", isAuthenticated: true, isLoading: false };
    // Default: each helper resolves with the same state passed in from
    // the test so the post-bump setState doesn't flip visibility.
    incrementBrowseSessions.mockImplementation(() =>
      Promise.resolve(makeState()),
    );
    dismissOnboardingBanner.mockResolvedValue(makeState());
  });

  it("renders nothing for anonymous visitors", () => {
    mockAuth = { token: null, isAuthenticated: false, isLoading: false };
    const { container } = render(<OnboardingBanner />);
    expect(container).toBeEmptyDOMElement();
    expect(getOnboardingState).not.toHaveBeenCalled();
  });

  it("renders the nudge when the server says banner.visible is true", async () => {
    getOnboardingState.mockResolvedValue(makeState());
    render(<OnboardingBanner />);
    expect(
      await screen.findByRole("link", { name: /finish setup/i }),
    ).toHaveAttribute("href", "/welcome");
  });

  it("renders nothing when banner.visible is false", async () => {
    getOnboardingState.mockResolvedValue(
      makeState({
        banner: {
          visible: false,
          dismissed_at: null,
          browse_sessions_since_skipped: 0,
        },
        skipped_entirely_at: null,
      }),
    );
    const { container } = render(<OnboardingBanner />);
    await waitFor(() => expect(getOnboardingState).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("bumps the browse-session counter at most once per browser session", async () => {
    getOnboardingState.mockResolvedValue(makeState());
    const { unmount } = render(<OnboardingBanner />);
    await screen.findByRole("link", { name: /finish setup/i });
    await waitFor(() =>
      expect(incrementBrowseSessions).toHaveBeenCalledTimes(1),
    );

    // A second mount in the same session should NOT bump again.
    unmount();
    render(<OnboardingBanner />);
    await waitFor(() => expect(getOnboardingState).toHaveBeenCalledTimes(2));
    expect(incrementBrowseSessions).toHaveBeenCalledTimes(1);
  });

  it("hides optimistically on dismiss", async () => {
    getOnboardingState.mockResolvedValue(makeState());
    render(<OnboardingBanner />);
    const btn = await screen.findByRole("button", {
      name: /dismiss onboarding banner/i,
    });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /finish setup/i })).toBeNull(),
    );
    expect(dismissOnboardingBanner).toHaveBeenCalledWith("jwt");
  });
});
