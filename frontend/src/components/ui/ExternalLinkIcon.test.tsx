/**
 * Tests for ExternalLinkIcon — the small "leaves the app" affordance.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ExternalLinkIcon from "@/components/ui/ExternalLinkIcon";

describe("ExternalLinkIcon", () => {
  it("renders an inline SVG marked aria-hidden so the surrounding text owns the label", () => {
    render(<ExternalLinkIcon />);
    const svg = screen.getByTestId("external-link-icon");
    expect(svg.tagName.toLowerCase()).toBe("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("forwards a className for color/size tweaks at the call site", () => {
    render(<ExternalLinkIcon className="text-accent" />);
    const svg = screen.getByTestId("external-link-icon");
    expect(svg.getAttribute("class")).toMatch(/text-accent/);
  });
});
