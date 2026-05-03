/**
 * Tests for the TestNotificationsCard.
 *
 * The card has two side-by-side actions (push + email) that each map
 * the same backend response shape onto a few specific UI strings. The
 * tests pin every result branch (success, no-vapid, no-subscriptions,
 * disabled, bounced address, no-email, generic error, rate-limit) so a
 * future refactor can't quietly change the message users see when
 * something goes wrong.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TestNotificationsCard } from "@/components/settings/TestNotificationsCard";
import { ApiRequestError } from "@/lib/api/client";

const sendTestPushToSelf = vi.fn();
const sendTestEmailToSelf = vi.fn();

vi.mock("@/lib/api/push", () => ({
  sendTestPushToSelf: (token: string) => sendTestPushToSelf(token),
}));

vi.mock("@/lib/api/email", () => ({
  sendTestEmailToSelf: (token: string) => sendTestEmailToSelf(token),
}));

beforeEach(() => {
  sendTestPushToSelf.mockReset();
  sendTestEmailToSelf.mockReset();
});

describe("TestNotificationsCard — push branch", () => {
  it("shows a success line with the device count when the dispatcher reports succeeded > 0", async () => {
    sendTestPushToSelf.mockResolvedValueOnce({
      attempted: 2,
      succeeded: 2,
      disabled: 0,
      skipped_no_vapid: false,
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Sent to 2 devices/i),
      ).toBeInTheDocument();
    });
  });

  it("singularizes 'device' for one subscription", async () => {
    sendTestPushToSelf.mockResolvedValueOnce({
      attempted: 1,
      succeeded: 1,
      disabled: 0,
      skipped_no_vapid: false,
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(screen.getByText(/Sent to 1 device\b/i)).toBeInTheDocument();
    });
  });

  it("warns when VAPID isn't configured on the server", async () => {
    sendTestPushToSelf.mockResolvedValueOnce({
      attempted: 0,
      succeeded: 0,
      disabled: 0,
      skipped_no_vapid: true,
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(screen.getByText(/Push isn't configured/i)).toBeInTheDocument();
    });
  });

  it("warns when the user has no active subscriptions", async () => {
    sendTestPushToSelf.mockResolvedValueOnce({
      attempted: 0,
      succeeded: 0,
      disabled: 0,
      skipped_no_vapid: false,
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/No active devices subscribed/i),
      ).toBeInTheDocument();
    });
  });

  it("warns when every subscription was disabled by the push service", async () => {
    sendTestPushToSelf.mockResolvedValueOnce({
      attempted: 1,
      succeeded: 0,
      disabled: 1,
      skipped_no_vapid: false,
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/endpoint is dead/i),
      ).toBeInTheDocument();
    });
  });

  it("disables the button while in flight", async () => {
    let resolve!: (v: unknown) => void;
    sendTestPushToSelf.mockImplementationOnce(
      () => new Promise((r) => (resolve = r)),
    );
    render(<TestNotificationsCard token="tok" />);
    const button = screen.getByRole("button", { name: /Send test push/i });
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    resolve({
      attempted: 1,
      succeeded: 1,
      disabled: 0,
      skipped_no_vapid: false,
    });
  });

  it("surfaces an HTTP 429 with a friendly rate-limit message", async () => {
    sendTestPushToSelf.mockRejectedValueOnce(
      new ApiRequestError(429, "RATE_LIMITED", "too many"),
    );
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(screen.getByText(/test send limit/i)).toBeInTheDocument();
    });
  });

  it("falls back to a generic error for non-API exceptions", async () => {
    sendTestPushToSelf.mockRejectedValueOnce(new Error("network down"));
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test push/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Could not send a test push/i),
      ).toBeInTheDocument();
    });
  });
});

describe("TestNotificationsCard — email branch", () => {
  it("shows the masked recipient in the success message", async () => {
    sendTestEmailToSelf.mockResolvedValueOnce({
      sent: true,
      to: "p***@example.test",
      reason: "sent",
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test email/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Sent to p\*\*\*@example\.test/i),
      ).toBeInTheDocument();
    });
  });

  it("warns specifically when the address has previously bounced", async () => {
    sendTestEmailToSelf.mockResolvedValueOnce({
      sent: false,
      to: "p***@example.test",
      reason: "bounced",
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test email/i }));
    await waitFor(() => {
      expect(screen.getByText(/previously bounced/i)).toBeInTheDocument();
    });
  });

  it("warns when no email is on file", async () => {
    sendTestEmailToSelf.mockResolvedValueOnce({
      sent: false,
      to: "",
      reason: "no_email",
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test email/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/No email address is on file/i),
      ).toBeInTheDocument();
    });
  });

  it("warns when delivery failed at the provider", async () => {
    sendTestEmailToSelf.mockResolvedValueOnce({
      sent: false,
      to: "p***@example.test",
      reason: "delivery_failed",
    });
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test email/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Email provider rejected/i),
      ).toBeInTheDocument();
    });
  });

  it("does not affect the push branch's status when email fails", async () => {
    sendTestEmailToSelf.mockRejectedValueOnce(new Error("boom"));
    render(<TestNotificationsCard token="tok" />);
    fireEvent.click(screen.getByRole("button", { name: /Send test email/i }));
    await waitFor(() => {
      expect(
        screen.getByText(/Could not send a test email/i),
      ).toBeInTheDocument();
    });
    // Push tile stays at idle.
    expect(
      screen.queryByText(/Sent to .* device/i),
    ).not.toBeInTheDocument();
  });
});
