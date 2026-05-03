/**
 * Tests for the /me/email/test client helper.
 *
 * The endpoint surface is small (one POST, one typed result), so
 * these tests just pin the URL, the method, and the token forwarding
 * — enough that a refactor that breaks any of those surfaces here
 * before reaching production.
 */

import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { sendTestEmailToSelf } from "./email";

const fetchJson = vi.fn();

vi.mock("@/lib/api/client", async () => {
  const actual = await vi.importActual<typeof import("./client")>("./client");
  return {
    ...actual,
    fetchJson: (...args: unknown[]) => (fetchJson as unknown as Mock)(...args),
  };
});

beforeEach(() => {
  fetchJson.mockReset();
});

describe("sendTestEmailToSelf", () => {
  it("POSTs to /me/email/test with the bearer token and unwraps data", async () => {
    fetchJson.mockResolvedValueOnce({
      data: { sent: true, to: "p***@example.test", reason: "sent" },
    });

    await expect(sendTestEmailToSelf("tok-3")).resolves.toEqual({
      sent: true,
      to: "p***@example.test",
      reason: "sent",
    });

    expect(fetchJson).toHaveBeenCalledWith(
      "/api/v1/me/email/test",
      expect.objectContaining({ method: "POST", token: "tok-3" }),
    );
  });

  it("propagates the bounced reason verbatim", async () => {
    fetchJson.mockResolvedValueOnce({
      data: { sent: false, to: "p***@example.test", reason: "bounced" },
    });

    const result = await sendTestEmailToSelf("tok");
    expect(result.sent).toBe(false);
    expect(result.reason).toBe("bounced");
  });
});
