"use client";

import { useState } from "react";
import { shortRef } from "@/lib/format";
import { cn } from "@/lib/cn";

/** A query citation. One click copies the full ref — an analyst will paste it
 *  into a ticket, so it must survive the trip. */
export function RefChip({ value, className }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable — the text is still selectable */
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={value}
      className={cn(
        "inline-flex max-w-full items-center gap-1 truncate rounded-sm bg-surface-2 px-1.5 py-0.5",
        "font-mono text-mono-sm text-fg-muted transition-colors duration-fast",
        "hover:bg-surface-3 hover:text-fg",
        className,
      )}
    >
      <span className="truncate">{shortRef(value)}</span>
      <span className="text-fg-subtle" aria-hidden>{copied ? "✓" : "⧉"}</span>
      <span className="sr-only">{copied ? "copied" : "copy citation"}</span>
    </button>
  );
}
