/**
 * Tests for robots.ts — Fix #2 ensures the Sitemap directive points
 * at the absolute /sitemap.xml URL derived from the configured base.
 */

import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/config", () => ({
  config: {
    baseUrl: "https://greenroom.test/",
    publicApiUrl: "https://api.greenroom.test",
  },
}));

import robots from "@/app/robots";

describe("robots.ts", () => {
  it("emits an absolute Sitemap URL pointing at /sitemap.xml", () => {
    const result = robots();
    // The trailing slash in baseUrl must be stripped so the URL has
    // exactly one slash between host and sitemap path.
    expect(result.sitemap).toBe("https://greenroom.test/sitemap.xml");
  });

  it("allows the root path for every crawler rule", () => {
    const result = robots();
    expect(Array.isArray(result.rules)).toBe(true);
    const rules = result.rules as Array<{ allow?: string }>;
    for (const rule of rules) {
      expect(rule.allow).toBe("/");
    }
  });
});
