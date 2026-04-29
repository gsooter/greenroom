/**
 * Tests for the Tonight map genre FilterBar.
 *
 * FilterBar is pure UI — the only stateful concern is forwarding the
 * selected bucket key to a parent-provided onChange callback.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FilterBar, { genresForBucket } from "@/components/map/FilterBar";

describe("FilterBar", () => {
  it("renders an All pill plus one pill per genre bucket", () => {
    render(<FilterBar activeBucket={null} onChange={() => undefined} />);
    const radios = screen.getAllByRole("radio");
    // All + 5 buckets (green, blush, amber, coral, gold)
    expect(radios).toHaveLength(6);
    expect(radios[0]).toHaveTextContent("All");
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
  });

  it("marks the active bucket with aria-checked=true", () => {
    render(<FilterBar activeBucket="amber" onChange={() => undefined} />);
    const electronic = screen.getByRole("radio", { name: /electronic/i });
    expect(electronic).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /^all$/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("calls onChange with the bucket key when a pill is clicked", () => {
    const onChange = vi.fn();
    render(<FilterBar activeBucket={null} onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /hip-hop/i }));
    expect(onChange).toHaveBeenCalledWith("coral");
  });

  it("calls onChange with null when the All pill is clicked", () => {
    const onChange = vi.fn();
    render(<FilterBar activeBucket="green" onChange={onChange} />);
    fireEvent.click(screen.getByRole("radio", { name: /^all$/i }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("renders the optional count next to each pill when provided", () => {
    render(
      <FilterBar
        activeBucket={null}
        onChange={() => undefined}
        counts={{ total: 12, green: 4, amber: 0 }}
      />,
    );
    expect(screen.getByRole("radio", { name: /^all12$/i })).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /indie \/ rock4/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /electronic0/i }),
    ).toBeInTheDocument();
  });
});

describe("genresForBucket", () => {
  it("returns undefined when bucket is null", () => {
    expect(genresForBucket(null)).toBeUndefined();
  });

  it("returns the indie/rock list for green", () => {
    expect(genresForBucket("green")).toContain("indie");
    expect(genresForBucket("green")).toContain("punk");
  });

  it("returns the electronic list for amber", () => {
    expect(genresForBucket("amber")).toContain("house");
    expect(genresForBucket("amber")).toContain("techno");
  });
});
