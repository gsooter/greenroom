/**
 * Tests for the ScraperDashboard test-alert flow.
 *
 * Other behaviors of the dashboard (fleet table, run filters, per-venue
 * trigger) are covered by their respective backend route tests; this
 * suite focuses on the new "Send test alert" button so a regression in
 * the alerting verification flow surfaces immediately.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ScraperDashboard from "@/components/admin/ScraperDashboard";
import * as adminApi from "@/lib/api/admin";

vi.mock("@/lib/api/admin", async () => {
  const actual = await vi.importActual<typeof adminApi>("@/lib/api/admin");
  return {
    ...actual,
    getFleetSummary: vi.fn(),
    listScraperRuns: vi.fn(),
    triggerScraperRun: vi.fn(),
    sendTestAlert: vi.fn(),
  };
});

const mocked = adminApi as unknown as {
  getFleetSummary: ReturnType<typeof vi.fn>;
  listScraperRuns: ReturnType<typeof vi.fn>;
  triggerScraperRun: ReturnType<typeof vi.fn>;
  sendTestAlert: ReturnType<typeof vi.fn>;
};

describe("ScraperDashboard test-alert button", () => {
  beforeEach(() => {
    mocked.getFleetSummary.mockResolvedValue({
      enabled: 0,
      by_region: {},
      venues: [],
    });
    mocked.listScraperRuns.mockResolvedValue({
      runs: [],
      meta: { total: 0, page: 1, per_page: 50, has_next: false },
    });
    mocked.sendTestAlert.mockReset();
  });

  it("dispatches and renders the configured-channels banner on success", async () => {
    mocked.sendTestAlert.mockResolvedValue({
      delivered: true,
      slack_configured: true,
      email_configured: false,
      title: "Greenroom alert pipeline test",
      severity: "info",
    });

    render(<ScraperDashboard adminKey="the-key" signOut={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /send test alert/i }));

    await waitFor(() => {
      expect(mocked.sendTestAlert).toHaveBeenCalledWith("the-key");
    });
    expect(await screen.findByText(/test alert dispatched/i)).toBeInTheDocument();
    expect(screen.getByText(/slack/i)).toBeInTheDocument();
  });

  it("warns when no channels are configured", async () => {
    mocked.sendTestAlert.mockResolvedValue({
      delivered: true,
      slack_configured: false,
      email_configured: false,
      title: "Greenroom alert pipeline test",
      severity: "info",
    });

    render(<ScraperDashboard adminKey="the-key" signOut={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /send test alert/i }));

    expect(
      await screen.findByText(/no channels.*check env vars/i),
    ).toBeInTheDocument();
  });
});
