/**
 * Tests for the Toast primitive.
 *
 * Covers: useToast guard outside the provider, appearance on show(),
 * manual dismissal via the close button, and auto-dismiss after 5s.
 *
 * Uses fireEvent rather than userEvent to keep fake-timer interactions
 * simple — userEvent's internal delays fight with vi.useFakeTimers.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider, useToast } from "@/components/ui/Toast";

function Harness({ message = "hello" }: { message?: string }): JSX.Element {
  const { show } = useToast();
  return (
    <button type="button" onClick={() => show(message)}>
      trigger
    </button>
  );
}

describe("useToast", () => {
  it("throws a clear error when used outside the provider", () => {
    function Bad(): JSX.Element {
      useToast();
      return <div />;
    }
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Bad />)).toThrow(/useToast must be used/);
    spy.mockRestore();
  });
});

describe("ToastProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows a toast with the given message", () => {
    render(
      <ToastProvider>
        <Harness message="Sign in to save shows." />
      </ToastProvider>,
    );

    expect(
      screen.queryByText("Sign in to save shows."),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "trigger" }));

    expect(screen.getByText("Sign in to save shows.")).toBeInTheDocument();
  });

  it("dismisses on clicking the close button", () => {
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger" }));
    expect(screen.getByText("hello")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText("hello")).not.toBeInTheDocument();
  });

  it("auto-dismisses after 5 seconds", () => {
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "trigger" }));
    expect(screen.getByText("hello")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(screen.queryByText("hello")).not.toBeInTheDocument();
  });
});
