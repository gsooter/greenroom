/**
 * Tests for /login page layout.
 *
 * Fix #4: the page used a nested <main> with min-h-screen, which
 * stacked another full viewport height inside the AppShell main and
 * pushed the sign-in card past the visible area. The card should now
 * sit in normal document flow with its own vertical padding so it sits
 * close to the top nav on small screens.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const useAuthMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  useAuth: () => useAuthMock(),
}));

beforeEach(() => {
  useAuthMock.mockReturnValue({
    isAuthenticated: false,
    isLoading: false,
    login: vi.fn(),
  });
});

describe("LoginPage layout", () => {
  it("does not render a nested <main> element inside AppShell", () => {
    const { container } = render(<LoginPage />);
    // AppShell already provides the page-level <main>; rendering another
    // here would nest two main elements, which trips a11y checks and
    // doubles up the viewport-height styling.
    const mains = container.querySelectorAll("main");
    expect(mains.length).toBe(0);
  });

  it("does not stretch the wrapper to a full viewport height", () => {
    const { container } = render(<LoginPage />);
    const wrapper = container.firstElementChild as HTMLElement | null;
    expect(wrapper).not.toBeNull();
    const className = wrapper?.className ?? "";
    // min-h-screen here would stack a second 100vh on top of the
    // AppShell main, pushing the card off-screen below the fold.
    expect(className).not.toMatch(/\bmin-h-screen\b/);
  });

  it("renders the sign-in heading and email field", () => {
    render(<LoginPage />);
    expect(
      screen.getByRole("heading", { name: /sign in to greenroom/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/you@example\.com/i),
    ).toBeInTheDocument();
  });
});
