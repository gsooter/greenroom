/**
 * Tests for SaveEventButton.
 *
 * The button reads auth state, saved-set membership, and the toast
 * trigger from three hooks. We mock each module so we can drive exactly
 * the combinations we care about: anon click → toast (no toggle),
 * authed click → toggle, saved-vs-unsaved label and aria-pressed, and
 * the pill variant's copy.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SaveEventButton from "@/components/events/SaveEventButton";

const showToast = vi.fn();
const toggle = vi.fn();
const isSaved = vi.fn<(id: string) => boolean>(() => false);

let mockAuth: {
  isAuthenticated: boolean;
  isLoading: boolean;
} = {
  isAuthenticated: false,
  isLoading: false,
};

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ show: showToast }),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockAuth,
}));

vi.mock("@/lib/saved-events-context", () => ({
  useSavedEvents: () => ({ isSaved, toggle }),
}));

describe("SaveEventButton", () => {
  beforeEach(() => {
    showToast.mockReset();
    toggle.mockReset();
    isSaved.mockReset();
    isSaved.mockReturnValue(false);
    mockAuth = { isAuthenticated: false, isLoading: false };
  });

  it("shows a toast and does not toggle when the user is anonymous", () => {
    render(<SaveEventButton eventId="e-1" />);

    fireEvent.click(screen.getByRole("button"));

    expect(showToast).toHaveBeenCalledWith(
      "Sign in with Spotify to save shows.",
    );
    expect(toggle).not.toHaveBeenCalled();
  });

  it("calls toggle and does not toast when authenticated", () => {
    mockAuth = { isAuthenticated: true, isLoading: false };

    render(<SaveEventButton eventId="e-1" />);
    fireEvent.click(screen.getByRole("button"));

    expect(toggle).toHaveBeenCalledWith("e-1");
    expect(showToast).not.toHaveBeenCalled();
  });

  it("prevents the click from bubbling out of its event card", () => {
    mockAuth = { isAuthenticated: true, isLoading: false };
    const parentClick = vi.fn();

    render(
      <div onClick={parentClick}>
        <SaveEventButton eventId="e-1" />
      </div>,
    );

    fireEvent.click(screen.getByRole("button"));

    expect(parentClick).not.toHaveBeenCalled();
    expect(toggle).toHaveBeenCalledWith("e-1");
  });

  it("reflects saved state via aria-pressed and the 'Saved' label (pill)", () => {
    mockAuth = { isAuthenticated: true, isLoading: false };
    isSaved.mockReturnValue(true);

    render(<SaveEventButton eventId="e-1" variant="pill" />);

    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn).toHaveTextContent("Saved");
    expect(btn.getAttribute("aria-label")).toBe("Remove from saved shows");
  });

  it("renders the 'Save show' pill when not saved", () => {
    mockAuth = { isAuthenticated: true, isLoading: false };
    isSaved.mockReturnValue(false);

    render(<SaveEventButton eventId="e-1" variant="pill" />);

    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(btn).toHaveTextContent("Save show");
    expect(btn.getAttribute("aria-label")).toBe("Save this show");
  });

  it("ignores isSaved when the user is logged out", () => {
    // A stale saved-set (e.g. right after logout) must not flash as saved.
    mockAuth = { isAuthenticated: false, isLoading: false };
    isSaved.mockReturnValue(true);

    render(<SaveEventButton eventId="e-1" variant="pill" />);

    expect(screen.getByRole("button").getAttribute("aria-pressed")).toBe(
      "false",
    );
    expect(screen.getByRole("button")).toHaveTextContent("Save show");
  });

  it("disables the button while auth is loading", () => {
    mockAuth = { isAuthenticated: false, isLoading: true };

    render(<SaveEventButton eventId="e-1" />);

    expect(screen.getByRole("button")).toBeDisabled();
  });
});
