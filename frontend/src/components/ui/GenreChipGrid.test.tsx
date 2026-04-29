/**
 * Tests for the GenreChipGrid primitive.
 *
 * Covers: rendering all genres, active-state styling toggle, click
 * dispatch, and disabled-mode suppression.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GenreChipGrid } from "@/components/ui/GenreChipGrid";
import type { Genre } from "@/types";

const GENRES: Genre[] = [
  { slug: "rock", label: "Rock", emoji: "🎸" },
  { slug: "jazz", label: "Jazz", emoji: "🎷" },
  { slug: "indie", label: "Indie", emoji: "🪕" },
];

describe("GenreChipGrid", () => {
  it("renders every genre as a button", () => {
    render(
      <GenreChipGrid
        genres={GENRES}
        selected={new Set()}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /Rock/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Jazz/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Indie/ })).toBeInTheDocument();
  });

  it("marks selected genres with aria-pressed=true", () => {
    render(
      <GenreChipGrid
        genres={GENRES}
        selected={new Set(["jazz"])}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /Jazz/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /Rock/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("calls onToggle with the slug when a chip is clicked", () => {
    const onToggle = vi.fn();
    render(
      <GenreChipGrid
        genres={GENRES}
        selected={new Set()}
        onToggle={onToggle}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Indie/ }));
    expect(onToggle).toHaveBeenCalledWith("indie");
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when given an empty list", () => {
    const { container } = render(
      <GenreChipGrid
        genres={[]}
        selected={new Set()}
        onToggle={() => {}}
      />,
    );
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("disables every chip when disabled is true", () => {
    const onToggle = vi.fn();
    render(
      <GenreChipGrid
        genres={GENRES}
        selected={new Set()}
        onToggle={onToggle}
        disabled
      />,
    );
    for (const button of screen.getAllByRole("button")) {
      expect(button).toBeDisabled();
    }
    fireEvent.click(screen.getByRole("button", { name: /Rock/ }));
    expect(onToggle).not.toHaveBeenCalled();
  });
});
