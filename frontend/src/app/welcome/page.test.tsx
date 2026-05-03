/**
 * Tests for the /welcome page's revisit-mode behavior.
 *
 * The default flow (server state drives current step + redirect to
 * /for-you when complete) is exercised through E2E and via the
 * individual step component tests; these focus on the
 * ``?step=`` / ``?return=`` query-param escape hatch added so users
 * who already finished onboarding can still come back to follow more
 * artists from settings or the home page nudges.
 */

import { render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

import WelcomePage from "@/app/welcome/page";
import type { OnboardingState, User } from "@/types";

const mockReplace = vi.fn();
const searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: mockReplace, back: vi.fn() }),
  useSearchParams: () => searchParams,
}));

const useRequireAuthMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  useRequireAuth: () => useRequireAuthMock(),
}));

const getOnboardingState = vi.fn<(token: string) => Promise<OnboardingState>>();
const markStepComplete = vi.fn();
const skipOnboardingEntirely = vi.fn();

vi.mock("@/lib/api/onboarding", () => ({
  getOnboardingState: (token: string) =>
    (getOnboardingState as unknown as Mock)(token),
  markStepComplete: (...args: unknown[]) =>
    (markStepComplete as unknown as Mock)(...args),
  skipOnboardingEntirely: (...args: unknown[]) =>
    (skipOnboardingEntirely as unknown as Mock)(...args),
}));

vi.mock("@/components/welcome/TasteStep", () => ({
  TasteStep: ({ onDone }: { onDone: () => void }) => (
    <div data-testid="taste-step">
      <button type="button" onClick={onDone}>
        finish-taste
      </button>
    </div>
  ),
}));

vi.mock("@/components/welcome/VenuesStep", () => ({
  VenuesStep: () => <div data-testid="venues-step" />,
}));

vi.mock("@/components/welcome/MusicServicesStep", () => ({
  MusicServicesStep: () => <div data-testid="music-step" />,
}));

vi.mock("@/components/welcome/PasskeyStep", () => ({
  PasskeyStep: () => <div data-testid="passkey-step" />,
}));

vi.mock("@/components/welcome/WelcomeProgress", () => ({
  WelcomeProgress: () => <div data-testid="welcome-progress" />,
}));

const mockUser: User = {
  id: "u-1",
  email: "u@example.test",
  display_name: "Pat",
  avatar_url: null,
  city_id: null,
  digest_frequency: "weekly",
  genre_preferences: [],
  notification_settings: {},
  spotify_beta_access: false,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

function completedState(): OnboardingState {
  return {
    steps: { taste: true, venues: true, music_services: true, passkey: true },
    completed: true,
    skipped_entirely_at: null,
    banner: {
      visible: false,
      dismissed_at: null,
      browse_sessions_since_skipped: 0,
    },
  };
}

beforeEach(() => {
  mockReplace.mockReset();
  useRequireAuthMock.mockReset();
  getOnboardingState.mockReset();
  markStepComplete.mockReset();
  skipOnboardingEntirely.mockReset();
  searchParams.delete("step");
  searchParams.delete("return");

  useRequireAuthMock.mockReturnValue({
    user: mockUser,
    token: "tok",
    isLoading: false,
    isAuthenticated: true,
    refreshUser: vi.fn(),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("/welcome revisit mode", () => {
  it("redirects completed users to /for-you when no ?step is present", async () => {
    getOnboardingState.mockResolvedValue(completedState());
    render(<WelcomePage />);
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/for-you");
    });
  });

  it("renders the requested step for completed users when ?step=taste is set", async () => {
    searchParams.set("step", "taste");
    searchParams.set("return", "/settings");
    getOnboardingState.mockResolvedValue(completedState());

    render(<WelcomePage />);

    await waitFor(() => {
      expect(screen.getByTestId("taste-step")).toBeInTheDocument();
    });
    // Revisit mode never bounces.
    expect(mockReplace).not.toHaveBeenCalledWith("/for-you");
    // The progress strip is hidden because the user isn't doing the full
    // four-step flow.
    expect(screen.queryByTestId("welcome-progress")).toBeNull();
  });

  it("returns the user to ?return on done", async () => {
    searchParams.set("step", "taste");
    searchParams.set("return", "/settings");
    getOnboardingState.mockResolvedValue(completedState());
    markStepComplete.mockResolvedValue(completedState());

    render(<WelcomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("taste-step")).toBeInTheDocument(),
    );

    screen.getByRole("button", { name: "finish-taste" }).click();

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/settings");
    });
  });

  it("ignores unknown ?step values and falls through to the default flow", async () => {
    searchParams.set("step", "not-a-step");
    getOnboardingState.mockResolvedValue(completedState());
    render(<WelcomePage />);
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/for-you");
    });
  });

  it("rejects open-redirect ?return values and falls back to /settings", async () => {
    searchParams.set("step", "taste");
    searchParams.set("return", "//evil.example/steal");
    getOnboardingState.mockResolvedValue(completedState());
    markStepComplete.mockResolvedValue(completedState());

    render(<WelcomePage />);
    await waitFor(() =>
      expect(screen.getByTestId("taste-step")).toBeInTheDocument(),
    );
    screen.getByRole("button", { name: "finish-taste" }).click();
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/settings");
    });
  });
});
