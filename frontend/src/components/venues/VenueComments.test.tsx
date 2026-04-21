/**
 * Tests for VenueComments.
 *
 * The component hangs a lot of behavior off three external modules —
 * auth, toast, and the venue-comments API — so we mock those and drive
 * individual scenarios: anonymous read-only, authenticated submit,
 * vote toggle (up / clear / flip), category filter, and error handling.
 */

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VenueComments from "@/components/venues/VenueComments";
import type {
  VenueComment,
  VenueCommentCategory,
  VenueCommentSort,
} from "@/types";

const listMock = vi.fn();
const submitMock = vi.fn();
const voteMock = vi.fn();
const showToast = vi.fn();

let mockAuth: {
  isAuthenticated: boolean;
  isLoading: boolean;
  token: string | null;
} = {
  isAuthenticated: false,
  isLoading: false,
  token: null,
};

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ show: showToast }),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

vi.mock("@/lib/api/venue-comments", () => ({
  listVenueComments: (...args: unknown[]) => listMock(...args),
  submitVenueComment: (...args: unknown[]) => submitMock(...args),
  voteOnVenueComment: (...args: unknown[]) => voteMock(...args),
  deleteVenueComment: vi.fn(),
}));

vi.mock("@/lib/guest-session", () => ({
  getGuestSessionId: () => "guest-test",
}));

function makeComment(partial: Partial<VenueComment> = {}): VenueComment {
  return {
    id: "c1",
    venue_id: "v1",
    user_id: null,
    category: "vibes",
    body: "Great sound, get there early for the bar.",
    likes: 3,
    dislikes: 1,
    viewer_vote: null,
    created_at: new Date().toISOString(),
    updated_at: null,
    ...partial,
  };
}

describe("VenueComments", () => {
  beforeEach(() => {
    listMock.mockReset();
    submitMock.mockReset();
    voteMock.mockReset();
    showToast.mockReset();
    mockAuth = { isAuthenticated: false, isLoading: false, token: null };
  });

  it("renders fetched comments for an anonymous visitor", async () => {
    listMock.mockResolvedValueOnce({
      data: [makeComment({ body: "Seats up top have the best view." })],
      meta: { count: 1 },
    });

    render(<VenueComments slug="black-cat" />);

    await screen.findByText("Seats up top have the best view.");
    expect(
      screen.getByText(/Sign in to leave a tip/i),
    ).toBeInTheDocument();
  });

  it("renders empty state when no comments come back", async () => {
    listMock.mockResolvedValueOnce({ data: [], meta: { count: 0 } });

    render(<VenueComments slug="black-cat" />);

    await screen.findByText("No tips yet");
  });

  it("shows an error message if the API list call fails", async () => {
    listMock.mockRejectedValueOnce(new Error("boom"));

    render(<VenueComments slug="black-cat" />);

    await screen.findByText("Could not load comments.");
  });

  it("refetches with the chosen category", async () => {
    listMock.mockResolvedValue({ data: [], meta: { count: 0 } });

    render(<VenueComments slug="black-cat" />);

    await waitFor(() => expect(listMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "Tickets" }));

    await waitFor(() => {
      const lastCall = listMock.mock.calls.at(-1);
      expect(lastCall?.[2]?.category as VenueCommentCategory).toBe("tickets");
    });
  });

  it("refetches with a new sort mode", async () => {
    listMock.mockResolvedValue({ data: [], meta: { count: 0 } });

    render(<VenueComments slug="black-cat" />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("tab", { name: "new" }));

    await waitFor(() => {
      const lastCall = listMock.mock.calls.at(-1);
      expect(lastCall?.[2]?.sort as VenueCommentSort).toBe("new");
    });
  });

  it("applies an optimistic upvote and confirms with server counts", async () => {
    const comment = makeComment({ likes: 3, dislikes: 1, viewer_vote: null });
    listMock.mockResolvedValueOnce({ data: [comment], meta: { count: 1 } });
    voteMock.mockResolvedValueOnce({
      likes: 4,
      dislikes: 1,
      viewer_vote: 1,
    });

    render(<VenueComments slug="black-cat" />);

    await screen.findByText(comment.body);
    const upvote = screen.getByRole("button", { name: "Upvote" });
    // Optimistic: 3 → 4 likes, so net 4-1 = 3.
    fireEvent.click(upvote);

    // Server confirms matching counts; aria-pressed reflects state.
    await waitFor(() => {
      expect(upvote.getAttribute("aria-pressed")).toBe("true");
    });
    expect(voteMock).toHaveBeenCalledWith(
      "black-cat",
      "c1",
      null,
      1,
      "guest-test",
    );
  });

  it("rolls back the optimistic vote on server error", async () => {
    const comment = makeComment({ likes: 3, dislikes: 1, viewer_vote: null });
    listMock.mockResolvedValueOnce({ data: [comment], meta: { count: 1 } });
    voteMock.mockRejectedValueOnce(new Error("offline"));

    render(<VenueComments slug="black-cat" />);
    await screen.findByText(comment.body);

    fireEvent.click(screen.getByRole("button", { name: "Upvote" }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalled();
    });
    // After rollback the arrow is un-pressed again.
    const upvote = screen.getByRole("button", { name: "Upvote" });
    expect(upvote.getAttribute("aria-pressed")).toBe("false");
  });

  it("clears the vote when clicking the same arrow twice", async () => {
    const comment = makeComment({ likes: 4, dislikes: 1, viewer_vote: 1 });
    listMock.mockResolvedValueOnce({ data: [comment], meta: { count: 1 } });
    voteMock.mockResolvedValueOnce({
      likes: 3,
      dislikes: 1,
      viewer_vote: null,
    });

    render(<VenueComments slug="black-cat" />);
    await screen.findByText(comment.body);

    fireEvent.click(screen.getByRole("button", { name: "Upvote" }));

    await waitFor(() => {
      expect(voteMock).toHaveBeenCalledWith(
        "black-cat",
        "c1",
        null,
        0,
        "guest-test",
      );
    });
  });

  it("renders the composer for authenticated users and posts", async () => {
    mockAuth = {
      isAuthenticated: true,
      isLoading: false,
      token: "access-token",
    };
    listMock.mockResolvedValue({ data: [], meta: { count: 0 } });
    submitMock.mockResolvedValueOnce(makeComment({ id: "new-1" }));

    render(<VenueComments slug="black-cat" />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());

    const textarea = screen.getByLabelText("New comment");
    fireEvent.change(textarea, {
      target: { value: "Doors really open at 7:30, not 7." },
    });
    fireEvent.click(screen.getByRole("button", { name: /post tip/i }));

    await waitFor(() => {
      expect(submitMock).toHaveBeenCalledWith(
        "black-cat",
        "access-token",
        expect.objectContaining({
          body: "Doors really open at 7:30, not 7.",
          honeypot: "",
        }),
      );
    });
  });

  it("blocks submit when the body is too short", async () => {
    mockAuth = {
      isAuthenticated: true,
      isLoading: false,
      token: "access-token",
    };
    listMock.mockResolvedValue({ data: [], meta: { count: 0 } });

    render(<VenueComments slug="black-cat" />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("New comment"), {
      target: { value: "x" },
    });
    // Submit button is disabled under min-length, so simulate a direct submit.
    const form = screen.getByLabelText("New comment").closest("form");
    expect(form).not.toBeNull();
    await act(async () => {
      fireEvent.submit(form!);
    });
    expect(submitMock).not.toHaveBeenCalled();
  });

  it("renders the net vote count inside the comment card", async () => {
    const comment = makeComment({ likes: 5, dislikes: 2 });
    listMock.mockResolvedValueOnce({ data: [comment], meta: { count: 1 } });

    render(<VenueComments slug="black-cat" />);
    const article = (await screen.findByText(comment.body)).closest(
      "article",
    );
    expect(article).not.toBeNull();
    expect(within(article!).getByLabelText("3 net votes")).toBeInTheDocument();
  });
});
