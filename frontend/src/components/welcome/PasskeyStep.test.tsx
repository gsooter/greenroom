/**
 * Tests for PasskeyStep.
 *
 * The registration UI is gated on isWebAuthnSupported(). On an
 * unsupported browser we collapse to a Continue button so onboarding
 * never dead-ends on hardware it can't use. Skip always works.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PasskeyStep } from "@/components/welcome/PasskeyStep";

const startPasskeyRegistration = vi.fn();
const completePasskeyRegistration = vi.fn();
const isWebAuthnSupported = vi.fn();
const decodeRegistrationOptions = vi.fn();
const encodeRegistrationCredential = vi.fn();

vi.mock("@/lib/api/auth-identity", () => ({
  startPasskeyRegistration: (token: string) => startPasskeyRegistration(token),
  completePasskeyRegistration: (
    token: string,
    cred: unknown,
    state: string,
    label?: string,
  ) => completePasskeyRegistration(token, cred, state, label),
}));

vi.mock("@/lib/webauthn", () => ({
  isWebAuthnSupported: () => isWebAuthnSupported(),
  decodeRegistrationOptions: (o: unknown) => decodeRegistrationOptions(o),
  encodeRegistrationCredential: (c: unknown) => encodeRegistrationCredential(c),
}));

describe("PasskeyStep", () => {
  beforeEach(() => {
    startPasskeyRegistration.mockReset();
    completePasskeyRegistration.mockReset();
    isWebAuthnSupported.mockReset();
    decodeRegistrationOptions.mockReset();
    encodeRegistrationCredential.mockReset();
  });

  it("shows the register UI and skip button when WebAuthn is supported", () => {
    isWebAuthnSupported.mockReturnValue(true);
    render(<PasskeyStep token="jwt" onDone={vi.fn()} onSkip={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /Add a passkey/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Skip for now/i }),
    ).toBeInTheDocument();
    // No inline Continue button when the device can actually register.
    expect(screen.queryByRole("button", { name: /^Continue$/ })).toBeNull();
  });

  it("auto-presents a Continue button when WebAuthn is unsupported", () => {
    isWebAuthnSupported.mockReturnValue(false);
    const onDone = vi.fn();
    render(<PasskeyStep token="jwt" onDone={onDone} onSkip={vi.fn()} />);

    const cont = screen.getByRole("button", { name: /^Continue$/ });
    fireEvent.click(cont);
    expect(onDone).toHaveBeenCalled();
    expect(startPasskeyRegistration).not.toHaveBeenCalled();
  });

  it("invokes onSkip without any write", () => {
    isWebAuthnSupported.mockReturnValue(true);
    const onSkip = vi.fn();
    render(<PasskeyStep token="jwt" onDone={vi.fn()} onSkip={onSkip} />);

    fireEvent.click(screen.getByRole("button", { name: /Skip for now/i }));
    expect(onSkip).toHaveBeenCalled();
    expect(startPasskeyRegistration).not.toHaveBeenCalled();
  });
});
