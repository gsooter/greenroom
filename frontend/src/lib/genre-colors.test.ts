/**
 * Tests for the genre → pin color mapping used on the Tonight map.
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_MAP_COLOR,
  pinColorForGenres,
  pinColorStyle,
  pinColorVariable,
} from "@/lib/genre-colors";

describe("pinColorForGenres", () => {
  it("returns the default bucket when genres is null", () => {
    expect(pinColorForGenres(null)).toBe(DEFAULT_MAP_COLOR);
  });

  it("returns the default bucket for an empty list", () => {
    expect(pinColorForGenres([])).toBe(DEFAULT_MAP_COLOR);
  });

  it("maps indie / rock families to green", () => {
    expect(pinColorForGenres(["indie"])).toBe("green");
    expect(pinColorForGenres(["punk"])).toBe("green");
    expect(pinColorForGenres(["alternative"])).toBe("green");
  });

  it("maps pop / folk families to blush", () => {
    expect(pinColorForGenres(["pop"])).toBe("blush");
    expect(pinColorForGenres(["folk"])).toBe("blush");
  });

  it("maps electronic / dance to amber", () => {
    expect(pinColorForGenres(["electronic"])).toBe("amber");
    expect(pinColorForGenres(["EDM"])).toBe("amber");
  });

  it("maps hip-hop with either spelling to coral", () => {
    expect(pinColorForGenres(["hip-hop"])).toBe("coral");
    expect(pinColorForGenres(["hip hop"])).toBe("coral");
    expect(pinColorForGenres(["Rap"])).toBe("coral");
  });

  it("maps jazz / soul / rnb to gold", () => {
    expect(pinColorForGenres(["jazz"])).toBe("gold");
    expect(pinColorForGenres(["R&B"])).toBe("gold");
    expect(pinColorForGenres(["rnb"])).toBe("gold");
  });

  it("lets earlier groups win when an event spans two buckets", () => {
    expect(pinColorForGenres(["indie", "electronic"])).toBe("green");
    expect(pinColorForGenres(["pop", "hip-hop"])).toBe("blush");
  });

  it("falls back to the default bucket for unknown genres", () => {
    expect(pinColorForGenres(["metal", "classical"])).toBe(DEFAULT_MAP_COLOR);
  });

  it("trims whitespace and lowercases before matching", () => {
    expect(pinColorForGenres(["  Indie  "])).toBe("green");
  });
});

describe("pinColorStyle", () => {
  it("exposes --pin-color as a CSS variable", () => {
    const style = pinColorStyle("amber") as Record<string, string>;
    expect(style["--pin-color"]).toBe("var(--color-amber)");
  });
});

describe("pinColorVariable", () => {
  it("returns the raw CSS variable reference", () => {
    expect(pinColorVariable("gold")).toBe("var(--color-gold)");
    expect(pinColorVariable("navy")).toBe("var(--color-navy-dark)");
  });
});
