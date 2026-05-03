/**
 * Tests for CompactModeToggle.
 *
 * Verifies pressed-state markers track the stored preference and
 * that clicking either button writes through and re-renders the
 * toggle.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import CompactModeToggle from "@/components/home/CompactModeToggle";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("CompactModeToggle", () => {
  it("renders Comfortable as the default pressed option", () => {
    render(<CompactModeToggle />);
    expect(screen.getByRole("button", { name: "Comfortable" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Compact" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("flips the pressed-state and persists when Compact is clicked", () => {
    render(<CompactModeToggle />);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Compact" }));
    });
    expect(screen.getByRole("button", { name: "Compact" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(window.localStorage.getItem("greenroom.home.compact")).toBe("true");
  });

  it("rehydrates the pressed-state from storage on mount", () => {
    window.localStorage.setItem("greenroom.home.compact", "true");
    render(<CompactModeToggle />);
    expect(screen.getByRole("button", { name: "Compact" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
