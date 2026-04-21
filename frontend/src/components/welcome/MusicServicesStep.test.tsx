/**
 * Tests for MusicServicesStep.
 *
 * Spotify and Tidal are full-page redirects, so before navigating we
 * drop a sessionStorage marker that the auth callback reads to steer
 * the user back into /welcome. These tests pin that contract — the
 * marker must be written before window.location.href is assigned,
 * otherwise an OAuth round-trip would strand the user on /for-you.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MusicServicesStep } from "@/components/welcome/MusicServicesStep";

const startSpotifyOAuth = vi.fn();
const startTidalOAuth = vi.fn();
const getAppleMusicDeveloperToken = vi.fn();
const connectAppleMusic = vi.fn();
const getMyMusicConnections = vi.fn();
const authorizeAppleMusic = vi.fn();

vi.mock("@/lib/api/auth", () => ({
  startSpotifyOAuth: (token: string) => startSpotifyOAuth(token),
  startTidalOAuth: (token: string) => startTidalOAuth(token),
  getAppleMusicDeveloperToken: (token: string) =>
    getAppleMusicDeveloperToken(token),
  connectAppleMusic: (token: string, mut: string) =>
    connectAppleMusic(token, mut),
}));

vi.mock("@/lib/api/me", () => ({
  getMyMusicConnections: (token: string) => getMyMusicConnections(token),
}));

vi.mock("@/lib/musickit", () => ({
  authorizeAppleMusic: (opts: unknown) => authorizeAppleMusic(opts),
}));

describe("MusicServicesStep", () => {
  beforeEach(() => {
    startSpotifyOAuth.mockReset();
    startTidalOAuth.mockReset();
    getAppleMusicDeveloperToken.mockReset();
    connectAppleMusic.mockReset();
    getMyMusicConnections.mockReset();
    authorizeAppleMusic.mockReset();
    window.sessionStorage.clear();
    getMyMusicConnections.mockResolvedValue({ connections: [] });
  });

  it("sets the welcome-return marker before redirecting to Spotify", async () => {
    startSpotifyOAuth.mockResolvedValue({
      authorize_url: "https://accounts.spotify.com/authorize?x=1",
      state: "s",
    });
    // Stub out the real navigation so jsdom doesn't complain.
    const hrefSetter = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, set href(v: string) { hrefSetter(v); } },
    });

    render(
      <MusicServicesStep token="jwt" onDone={vi.fn()} onSkip={vi.fn()} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Connect Spotify/i }),
    );

    await waitFor(() =>
      expect(hrefSetter).toHaveBeenCalledWith(
        "https://accounts.spotify.com/authorize?x=1",
      ),
    );
    expect(window.sessionStorage.getItem("greenroom.welcome_return")).toBe(
      "music_services",
    );

    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
  });

  it("connects Apple Music inline without any redirect marker", async () => {
    getAppleMusicDeveloperToken.mockResolvedValue({ developer_token: "dev-t" });
    authorizeAppleMusic.mockResolvedValue("music-user-token");
    connectAppleMusic.mockResolvedValue(undefined);

    render(
      <MusicServicesStep token="jwt" onDone={vi.fn()} onSkip={vi.fn()} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Connect Apple Music/i }),
    );

    await waitFor(() =>
      expect(connectAppleMusic).toHaveBeenCalledWith("jwt", "music-user-token"),
    );
    // Apple Music stays on-page — no welcome-return marker needed.
    expect(window.sessionStorage.getItem("greenroom.welcome_return")).toBeNull();
  });

  it("invokes onSkip without any write", async () => {
    const onSkip = vi.fn();
    render(
      <MusicServicesStep token="jwt" onDone={vi.fn()} onSkip={onSkip} />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /Skip for now/i }),
    );
    expect(onSkip).toHaveBeenCalled();
    expect(startSpotifyOAuth).not.toHaveBeenCalled();
    expect(startTidalOAuth).not.toHaveBeenCalled();
  });

  it("advances via onDone with Continue without any connection", async () => {
    const onDone = vi.fn();
    render(
      <MusicServicesStep token="jwt" onDone={onDone} onSkip={vi.fn()} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Continue without/i }),
    );
    expect(onDone).toHaveBeenCalled();
  });
});
