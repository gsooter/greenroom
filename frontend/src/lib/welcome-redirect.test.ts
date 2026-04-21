/**
 * Tests for the post-auth redirect helpers.
 *
 * ``resolvePostAuthDestination`` sends the user to /welcome while any
 * step is open and falls back on errors so a backend blip never traps
 * them on a loading page. ``consumeWelcomeReturnFlag`` is a one-shot
 * sessionStorage marker — once consumed it should be cleared.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  consumeWelcomeReturnFlag,
  resolvePostAuthDestination,
} from "@/lib/welcome-redirect";

const getState = vi.fn();

vi.mock("@/lib/api/onboarding", () => ({
  getOnboardingState: (token: string) => getState(token),
}));

describe("resolvePostAuthDestination", () => {
  beforeEach(() => {
    getState.mockReset();
  });

  it("routes to /welcome when onboarding is not complete", async () => {
    getState.mockResolvedValue({
      steps: {
        taste: true,
        venues: false,
        music_services: false,
        passkey: false,
      },
      completed: false,
    });
    await expect(resolvePostAuthDestination("jwt")).resolves.toBe("/welcome");
  });

  it("falls back to /for-you when onboarding is already completed", async () => {
    getState.mockResolvedValue({
      steps: {
        taste: true,
        venues: true,
        music_services: true,
        passkey: true,
      },
      completed: true,
    });
    await expect(resolvePostAuthDestination("jwt")).resolves.toBe("/for-you");
  });

  it("honors an explicit fallback", async () => {
    getState.mockResolvedValue({ completed: true });
    await expect(resolvePostAuthDestination("jwt", "/saved")).resolves.toBe(
      "/saved",
    );
  });

  it("falls back instead of throwing when the state call fails", async () => {
    getState.mockRejectedValue(new Error("boom"));
    await expect(resolvePostAuthDestination("jwt")).resolves.toBe("/for-you");
  });
});

describe("consumeWelcomeReturnFlag", () => {
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("returns the stored value and clears it", () => {
    window.sessionStorage.setItem("greenroom.welcome_return", "music_services");
    expect(consumeWelcomeReturnFlag()).toBe("music_services");
    expect(window.sessionStorage.getItem("greenroom.welcome_return")).toBeNull();
  });

  it("returns null when no flag is set", () => {
    expect(consumeWelcomeReturnFlag()).toBeNull();
  });
});
