/**
 * Tests for the AdminKeyGate localStorage-backed prompt.
 *
 * Covers: prompt rendered when no key is stored, submitting persists
 * the key and reveals children, signOut clears storage and re-shows
 * the prompt, an existing key skips the prompt entirely.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import AdminKeyGate from "@/components/admin/AdminKeyGate";

const STORAGE_KEY = "greenroom.adminKey";

describe("AdminKeyGate", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("shows the prompt and hides children when no key is stored", () => {
    render(
      <AdminKeyGate>{() => <div>secret content</div>}</AdminKeyGate>,
    );
    expect(screen.getByRole("heading", { name: /admin sign-in/i })).toBeInTheDocument();
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("persists the key and reveals children on submit", () => {
    render(
      <AdminKeyGate>
        {(key) => <div>secret={key}</div>}
      </AdminKeyGate>,
    );

    const input = screen.getByLabelText(/admin key/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "the-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(screen.getByText("secret=the-secret")).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("the-secret");
  });

  it("renders children directly when a key is already stored", () => {
    window.localStorage.setItem(STORAGE_KEY, "stored-key");
    render(
      <AdminKeyGate>{(key) => <div>secret={key}</div>}</AdminKeyGate>,
    );
    expect(screen.getByText("secret=stored-key")).toBeInTheDocument();
  });

  it("clears the key and re-shows the prompt on signOut", () => {
    window.localStorage.setItem(STORAGE_KEY, "stored-key");
    render(
      <AdminKeyGate>
        {(_, signOut) => (
          <button type="button" onClick={signOut}>
            sign out
          </button>
        )}
      </AdminKeyGate>,
    );

    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));

    expect(screen.getByRole("heading", { name: /admin sign-in/i })).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("ignores empty or whitespace-only submissions", () => {
    render(
      <AdminKeyGate>{() => <div>secret content</div>}</AdminKeyGate>,
    );

    const button = screen.getByRole("button", { name: /sign in/i }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    const input = screen.getByLabelText(/admin key/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    expect(button.disabled).toBe(true);
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });
});
