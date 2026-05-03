/**
 * Settings card with two buttons that fire a test push and a test
 * email at the current user. Lives at the top of /settings/notifications
 * so people can verify the channels they just configured actually work.
 *
 * Both buttons share the same conservative pattern: optimistic disable
 * while the request is in flight, a typed status banner on completion,
 * and a clear-text reason on every error path so users aren't left
 * with "something went wrong."
 */

"use client";

import { useCallback, useState } from "react";

import { ApiRequestError } from "@/lib/api/client";
import { sendTestEmailToSelf, type TestEmailResult } from "@/lib/api/email";
import { sendTestPushToSelf, type TestPushResult } from "@/lib/api/push";

interface Props {
  token: string;
}

type Channel = "push" | "email";
type ChannelStatus =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "ok"; message: string }
  | { kind: "warn"; message: string }
  | { kind: "error"; message: string };

const INITIAL: Record<Channel, ChannelStatus> = {
  push: { kind: "idle" },
  email: { kind: "idle" },
};

function describePushResult(result: TestPushResult): ChannelStatus {
  if (result.skipped_no_vapid) {
    return {
      kind: "warn",
      message:
        "Push isn't configured on the server yet. Ask an admin to set the VAPID keys.",
    };
  }
  if (result.attempted === 0) {
    return {
      kind: "warn",
      message:
        "No active devices subscribed. Open the PWA on your phone and tap Enable in the notification prompt.",
    };
  }
  if (result.succeeded > 0) {
    const noun = result.succeeded === 1 ? "device" : "devices";
    return {
      kind: "ok",
      message: `Sent to ${result.succeeded} ${noun}. It should appear within a few seconds.`,
    };
  }
  if (result.disabled > 0) {
    return {
      kind: "warn",
      message:
        "Every subscribed device reported the endpoint is dead. Re-enable from your phone's PWA.",
    };
  }
  return {
    kind: "warn",
    message: "Push service rejected the send. Try again in a moment.",
  };
}

function describeEmailResult(result: TestEmailResult): ChannelStatus {
  if (result.sent) {
    return {
      kind: "ok",
      message: `Sent to ${result.to}. Check your inbox.`,
    };
  }
  if (result.reason === "bounced") {
    return {
      kind: "warn",
      message: `${result.to} previously bounced. Update your address before retrying.`,
    };
  }
  if (result.reason === "no_email") {
    return {
      kind: "warn",
      message: "No email address is on file for your account.",
    };
  }
  return {
    kind: "warn",
    message: "Email provider rejected the send. Try again in a moment.",
  };
}

function toErrorMessage(err: unknown, channel: Channel): string {
  if (err instanceof ApiRequestError) {
    if (err.status === 429) {
      return "You've hit the test send limit. Try again in a few minutes.";
    }
    return err.message;
  }
  return channel === "push"
    ? "Could not send a test push. Try again in a moment."
    : "Could not send a test email. Try again in a moment.";
}

export function TestNotificationsCard({ token }: Props): JSX.Element {
  const [status, setStatus] = useState<Record<Channel, ChannelStatus>>(INITIAL);

  const setChannel = useCallback(
    (channel: Channel, next: ChannelStatus) => {
      setStatus((prev) => ({ ...prev, [channel]: next }));
    },
    [],
  );

  const handleSendPush = useCallback(async () => {
    setChannel("push", { kind: "sending" });
    try {
      const result = await sendTestPushToSelf(token);
      setChannel("push", describePushResult(result));
    } catch (err) {
      setChannel("push", { kind: "error", message: toErrorMessage(err, "push") });
    }
  }, [token, setChannel]);

  const handleSendEmail = useCallback(async () => {
    setChannel("email", { kind: "sending" });
    try {
      const result = await sendTestEmailToSelf(token);
      setChannel("email", describeEmailResult(result));
    } catch (err) {
      setChannel("email", {
        kind: "error",
        message: toErrorMessage(err, "email"),
      });
    }
  }, [token, setChannel]);

  return (
    <section
      aria-labelledby="test-notifications-heading"
      className="rounded-lg border border-border bg-bg-white p-4"
    >
      <h2
        id="test-notifications-heading"
        className="text-base font-semibold text-text-primary"
      >
        Test your notifications
      </h2>
      <p className="mt-1 text-xs text-text-secondary">
        Send yourself a sample push or email to confirm everything is wired up.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <ChannelTile
          title="Send a test push"
          description="Goes to every device where you've enabled notifications in the Greenroom PWA."
          buttonLabel="Send test push"
          status={status.push}
          onClick={() => void handleSendPush()}
        />
        <ChannelTile
          title="Send a test email"
          description="Sends a sample email to the address on your account. You'll see exactly what a real notification looks like."
          buttonLabel="Send test email"
          status={status.email}
          onClick={() => void handleSendEmail()}
        />
      </div>
    </section>
  );
}

function ChannelTile({
  title,
  description,
  buttonLabel,
  status,
  onClick,
}: {
  title: string;
  description: string;
  buttonLabel: string;
  status: ChannelStatus;
  onClick: () => void;
}): JSX.Element {
  const sending = status.kind === "sending";
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border/60 bg-bg-base p-3">
      <div>
        <p className="text-sm font-medium text-text-primary">{title}</p>
        <p className="mt-0.5 text-xs text-text-secondary">{description}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onClick}
          disabled={sending}
          className="rounded-full px-4 py-2 text-xs font-semibold disabled:opacity-50"
          style={{
            background: "var(--color-green-primary)",
            color: "var(--color-text-inverse)",
          }}
        >
          {sending ? "Sending…" : buttonLabel}
        </button>
        <StatusInline status={status} />
      </div>
    </div>
  );
}

function StatusInline({ status }: { status: ChannelStatus }): JSX.Element | null {
  if (status.kind === "idle" || status.kind === "sending") return null;
  const role = status.kind === "error" ? "alert" : "status";
  const color =
    status.kind === "ok"
      ? "var(--color-green-primary)"
      : status.kind === "warn"
        ? "var(--color-text-secondary)"
        : "var(--color-blush-accent)";
  return (
    <span role={role} className="text-xs" style={{ color }}>
      {status.message}
    </span>
  );
}

export default TestNotificationsCard;
