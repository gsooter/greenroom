/**
 * Tests for FeedbackWidget.
 *
 * Cover: pill renders by default, dismiss hides it for the session,
 * modal submit posts the trimmed payload via submitFeedback, signed-in
 * users hide the email field and the route still gets called, errors
 * surface inline without closing the modal.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import FeedbackWidget from "@/components/feedback/FeedbackWidget";
import type { User } from "@/types";

const submitMock = vi.hoisted(() => vi.fn());

interface MockAuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  token: string | null;
}

let mockAuth: MockAuthState = {
  user: null,
  isAuthenticated: false,
  isLoading: false,
  token: null,
};

vi.mock("@/lib/api/feedback", () => ({
  submitFeedback: submitMock,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

function userFixture(overrides: Partial<User> = {}): User {
  return {
    id: "u-1",
    email: "fan@example.com",
    display_name: "Fan",
    avatar_url: null,
    city_id: null,
    digest_frequency: "weekly",
    genre_preferences: [],
    notification_settings: {},
    spotify_beta_access: false,
    last_login_at: null,
    created_at: "2026-04-01T00:00:00Z",
    ...overrides,
  };
}

describe("FeedbackWidget", () => {
  beforeEach(() => {
    submitMock.mockReset();
    window.sessionStorage.clear();
    mockAuth = {
      user: null,
      isAuthenticated: false,
      isLoading: false,
      token: null,
    };
  });

  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("renders the pill by default", () => {
    render(<FeedbackWidget />);
    expect(screen.getByTestId("feedback-pill")).toBeInTheDocument();
  });

  it("hides the pill for the session when dismissed", () => {
    render(<FeedbackWidget />);
    fireEvent.click(
      screen.getByRole("button", { name: /dismiss feedback prompt/i }),
    );
    expect(screen.queryByTestId("feedback-pill")).not.toBeInTheDocument();
    expect(window.sessionStorage.getItem("greenroom.feedback.dismissed")).toBe(
      "1",
    );
  });

  it("opens the modal and submits trimmed message + chosen kind for anon users", async () => {
    submitMock.mockResolvedValueOnce({
      id: "f1",
      kind: "bug",
      message: "broken",
      email: null,
      page_url: null,
      is_resolved: false,
      created_at: "2026-04-27T00:00:00Z",
    });

    render(<FeedbackWidget />);
    fireEvent.click(screen.getByTestId("feedback-pill"));

    fireEvent.click(screen.getByRole("button", { name: /^Bug$/i }));
    fireEvent.change(screen.getByLabelText(/what's on your mind/i), {
      target: { value: "  broken  " },
    });
    fireEvent.change(screen.getByLabelText(/email \(optional\)/i), {
      target: { value: "anon@example.com" },
    });

    fireEvent.submit(
      screen.getByRole("button", { name: /send feedback/i }).closest("form")!,
    );

    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));
    const [payload] = submitMock.mock.calls[0]!;
    expect(payload).toMatchObject({
      message: "broken",
      kind: "bug",
      email: "anon@example.com",
    });
    await screen.findByRole("heading", { name: /thanks/i });
  });

  it("hides the email field and forwards the bearer token when signed in", async () => {
    mockAuth = {
      user: userFixture({ email: "me@example.com" }),
      isAuthenticated: true,
      isLoading: false,
      token: "tok-123",
    };
    submitMock.mockResolvedValueOnce({
      id: "f2",
      kind: "general",
      message: "hi",
      email: "me@example.com",
      page_url: null,
      is_resolved: false,
      created_at: "2026-04-27T00:00:00Z",
    });

    render(<FeedbackWidget />);
    fireEvent.click(screen.getByTestId("feedback-pill"));

    expect(
      screen.queryByLabelText(/email \(optional\)/i),
    ).not.toBeInTheDocument();
    expect(screen.getByText("me@example.com")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/what's on your mind/i), {
      target: { value: "hi" },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: /send feedback/i }).closest("form")!,
    );

    await waitFor(() => expect(submitMock).toHaveBeenCalledTimes(1));
    const [payload, token] = submitMock.mock.calls[0]!;
    expect(payload.email).toBeNull();
    expect(token).toBe("tok-123");
  });

  it("shows an inline error when the API call fails", async () => {
    submitMock.mockRejectedValueOnce(new Error("nope"));
    render(<FeedbackWidget />);
    fireEvent.click(screen.getByTestId("feedback-pill"));

    fireEvent.change(screen.getByLabelText(/what's on your mind/i), {
      target: { value: "broken" },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: /send feedback/i }).closest("form")!,
    );

    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent(/nope/i);
    expect(screen.queryByRole("heading", { name: /thanks/i })).toBeNull();
  });

  it("disables the submit button when the message is empty", () => {
    render(<FeedbackWidget />);
    fireEvent.click(screen.getByTestId("feedback-pill"));
    const button = screen.getByRole("button", {
      name: /send feedback/i,
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
