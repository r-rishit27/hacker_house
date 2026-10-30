"use client";

/**
 * Whether the event stream is attached, and honestly which kind of not.
 *
 * `reconnecting` is its own state rather than an error: the browser retries
 * on the server's `retry:` hint and resumes from the last sequence number, so
 * a blip costs nothing. `warming` is its own state too — the Savanna
 * workspace auto-stops and takes about 45 seconds to wake, and reporting that
 * as a failure would be wrong three times a morning.
 */

import type { ConnectionState } from "@/lib/hooks";
import { cn } from "@/lib/cn";

const LOOK: Record<ConnectionState, { label: string; dot: string; text: string }> = {
  idle: { label: "Not streaming", dot: "bg-fg-subtle", text: "text-fg-muted" },
  connecting: { label: "Connecting", dot: "bg-warning animate-pulse", text: "text-warning" },
  live: { label: "Live", dot: "bg-success", text: "text-success" },
  reconnecting: {
    label: "Reconnecting — will resume where it stopped",
    dot: "bg-warning animate-pulse",
    text: "text-warning",
  },
  closed: { label: "Run finished", dot: "bg-fg-subtle", text: "text-fg-muted" },
};

export function ConnectionBadge({ state, seq }: { state: ConnectionState; seq: number }) {
  const look = LOOK[state];
  return (
    <span
      className={cn("inline-flex items-center gap-2 text-caption", look.text)}
      aria-live="polite"
    >
      <span aria-hidden className={cn("inline-block h-2 w-2 rounded-full", look.dot)} />
      {look.label}
      {seq > 0 ? <span className="font-mono text-fg-subtle">seq {seq}</span> : null}
    </span>
  );
}
