/**
 * Tests for DeleteAccountModal.
 *
 * The destructive button is gated on the user typing "delete"
 * (case-insensitive). The tests below pin that gate, the cancel paths,
 * and inline-error rendering.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeleteAccountModal } from "@/components/settings/DeleteAccountModal";

describe("DeleteAccountModal", () => {
  it("disables confirm until the user types 'delete'", () => {
    const onConfirm = vi.fn();
    render(
      <DeleteAccountModal
        email="user@example.test"
        busy={false}
        error={null}
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );

    const confirmBtn = screen.getByRole("button", {
      name: /deactivate account/i,
    });
    expect(confirmBtn).toBeDisabled();

    fireEvent.click(confirmBtn);
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "wrong" },
    });
    expect(confirmBtn).toBeDisabled();

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "DELETE" },
    });
    expect(confirmBtn).not.toBeDisabled();

    fireEvent.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("renders the user's email so they can double-check", () => {
    render(
      <DeleteAccountModal
        email="user@example.test"
        busy={false}
        error={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByText("user@example.test")).toBeInTheDocument();
  });

  it("calls onCancel when the cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(
      <DeleteAccountModal
        email="user@example.test"
        busy={false}
        error={null}
        onCancel={onCancel}
        onConfirm={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when Escape is pressed", () => {
    const onCancel = vi.fn();
    render(
      <DeleteAccountModal
        email="user@example.test"
        busy={false}
        error={null}
        onCancel={onCancel}
        onConfirm={() => {}}
      />,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("shows an inline error and keeps the modal open", () => {
    render(
      <DeleteAccountModal
        email="user@example.test"
        busy={false}
        error="Could not deactivate account."
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(
      screen.getByText("Could not deactivate account."),
    ).toBeInTheDocument();
  });

  it("shows a busy label and ignores clicks while in flight", () => {
    const onConfirm = vi.fn();
    render(
      <DeleteAccountModal
        email="user@example.test"
        busy={true}
        error={null}
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "delete" },
    });
    const btn = screen.getByRole("button", { name: /deactivating/i });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
