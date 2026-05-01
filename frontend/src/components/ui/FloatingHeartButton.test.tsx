/**
 * Tests for FloatingHeartButton.
 *
 * The component is a pure presentational primitive — it owns the
 * markup, the saved-vs-unsaved data attribute, and the click contract.
 * The actual color/blur layering lives in globals.css and is keyed off
 * the .floating-heart-btn class plus [data-saved], so these tests
 * verify those hooks are present rather than asserting computed style
 * (jsdom doesn't load globals.css).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FloatingHeartButton from "@/components/ui/FloatingHeartButton";

function renderButton(
  overrides: Partial<React.ComponentProps<typeof FloatingHeartButton>> = {},
): HTMLButtonElement {
  const props = {
    saved: false,
    onClick: vi.fn(),
    ariaLabel: "Save this show",
    ...overrides,
  };
  render(<FloatingHeartButton {...props} />);
  return screen.getByRole("button") as HTMLButtonElement;
}

describe("FloatingHeartButton", () => {
  it("renders the unsaved state with data-saved=false and aria-pressed=false", () => {
    const btn = renderButton({ saved: false });
    expect(btn.getAttribute("data-saved")).toBe("false");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("renders the saved state with data-saved=true and aria-pressed=true", () => {
    const btn = renderButton({ saved: true, ariaLabel: "Remove from saved" });
    expect(btn.getAttribute("data-saved")).toBe("true");
    expect(btn.getAttribute("aria-pressed")).toBe("true");
  });

  it("applies the .floating-heart-btn class so the 44×44 glass styling rule kicks in", () => {
    // The 44×44 tap target, tinted background, blur, border, and shadow
    // all live in globals.css under .floating-heart-btn. Asserting the
    // class is present is the in-jsdom proxy for asserting the rule
    // applies in a real browser.
    const btn = renderButton();
    expect(btn.className).toContain("floating-heart-btn");
  });

  it("uses an unfilled heart in the unsaved state", () => {
    renderButton({ saved: false });
    const svg = screen.getByRole("button").querySelector("svg");
    expect(svg?.getAttribute("fill")).toBe("none");
    expect(svg?.getAttribute("stroke")).toBe("currentColor");
  });

  it("uses a filled heart in the saved state", () => {
    renderButton({ saved: true, ariaLabel: "Remove from saved" });
    const svg = screen.getByRole("button").querySelector("svg");
    expect(svg?.getAttribute("fill")).toBe("currentColor");
    expect(svg?.getAttribute("stroke")).toBe("currentColor");
  });

  it("exposes the supplied aria-label so screen readers describe the action", () => {
    const btn = renderButton({ ariaLabel: "Remove from saved shows" });
    expect(btn.getAttribute("aria-label")).toBe("Remove from saved shows");
  });

  it("invokes onClick with the original mouse event when clicked", () => {
    const onClick = vi.fn();
    const btn = renderButton({ onClick });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not fire onClick while disabled", () => {
    const onClick = vi.fn();
    const btn = renderButton({ onClick, disabled: true });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("flips aria-pressed and data-saved when the saved prop changes", () => {
    // Optimistic updates flow through a parent re-rendering with a new
    // saved prop — the button itself owns no state, so a prop flip must
    // immediately swap the rendered indicators.
    const onClick = vi.fn();
    const { rerender } = render(
      <FloatingHeartButton
        saved={false}
        onClick={onClick}
        ariaLabel="Save this show"
      />,
    );

    let btn = screen.getByRole("button");
    expect(btn.getAttribute("data-saved")).toBe("false");

    rerender(
      <FloatingHeartButton
        saved={true}
        onClick={onClick}
        ariaLabel="Remove from saved shows"
      />,
    );

    btn = screen.getByRole("button");
    expect(btn.getAttribute("data-saved")).toBe("true");
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn.getAttribute("aria-label")).toBe("Remove from saved shows");
  });
});
