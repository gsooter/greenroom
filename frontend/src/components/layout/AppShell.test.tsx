/**
 * Tests for AppShell — covers the sitemap link wiring (Fix #2).
 *
 * AppShell mostly wraps children with the global nav + footer, so the
 * tests focus on the small contracts the rest of the app relies on
 * staying stable: the footer sitemap link must point to the actual
 * Next-generated /sitemap.xml route.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AppShell from "@/components/layout/AppShell";

vi.mock("@/components/layout/TopNav", () => ({
  default: () => <header data-testid="top-nav" />,
}));

vi.mock("@/components/layout/MobileBottomNav", () => ({
  default: () => <nav data-testid="bottom-nav" />,
}));

vi.mock("@/components/feedback/FeedbackWidget", () => ({
  default: () => null,
}));

vi.mock("@/components/onboarding/OnboardingBanner", () => ({
  OnboardingBanner: () => null,
}));

describe("AppShell footer", () => {
  it("renders the sitemap link pointing at /sitemap.xml", () => {
    render(
      <AppShell>
        <main>page body</main>
      </AppShell>,
    );

    const link = screen.getByRole("link", { name: /^sitemap$/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/sitemap.xml");
  });
});
