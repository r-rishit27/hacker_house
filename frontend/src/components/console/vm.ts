/**
 * The mapping from what the backend says to what the console draws.
 *
 * Every function here is pure and total: given a value from the contract it
 * returns a token reference, never a colour. `var(--danger)` is a string an
 * SVG `stroke` accepts and a Tailwind class cannot express, which is the only
 * reason raw `var(...)` appears at all — see DESIGN_SYSTEM.md §7.
 *
 * Keeping this in one file is what stops "fraud is red" from being decided
 * nineteen times.
 */

import type {
  Action,
  EvidenceSource,
  Route,
  TriggerType,
  Verdict,
} from "@/lib/generated/contract";

import type { IconName } from "@/components/console/icons";

export type Tone = "neutral" | "accent" | "danger" | "success" | "warning" | "info";

/** The CSS variable a tone resolves to, for SVG attributes. */
export function toneVar(tone: Tone): string {
  return tone === "neutral" ? "var(--fg-subtle)" : `var(--${tone})`;
}

/** Its tint, for a fill behind text of the same tone. */
export function toneTint(tone: Tone): string {
  return tone === "neutral" ? "var(--surface-3)" : `var(--${tone}-tint)`;
}

// ── verdicts, routes, sources ────────────────────────────────────────────────

export function verdictTone(verdict: Verdict | "investigating" | "queued" | null): Tone {
  switch (verdict) {
    case "fraud":
      return "danger";
    case "legitimate":
      return "success";
    case "uncertain":
      return "warning";
    default:
      return "neutral";
  }
}

/** auto executes itself; L1 waits for a team lead; L2 for a fraud manager. */
export function routeTone(route: Route): Tone {
  return route === "auto" ? "success" : route === "L1" ? "warning" : "danger";
}

export function sourceTone(source: EvidenceSource): Tone {
  switch (source) {
    case "graph":
      return "accent";
    case "document":
      return "info";
    case "customer":
      return "warning";
    default:
      return "neutral";
  }
}

export function sourceIcon(source: EvidenceSource): IconName {
  switch (source) {
    case "graph":
      return "ring";
    case "document":
      return "doc";
    case "customer":
      return "ask";
    default:
      return "mail";
  }
}

export function triggerIcon(trigger: TriggerType): IconName {
  switch (trigger) {
    case "customer_report":
      return "user";
    case "analyst_request":
      return "ring";
    default:
      return "alert";
  }
}

export function triggerTone(trigger: TriggerType): Tone {
  switch (trigger) {
    case "customer_report":
      return "warning";
    case "analyst_request":
      return "info";
    default:
      return "accent";
  }
}

/** Actions that end a case one way or the other read differently from probes. */
export function actionTone(action: Action): Tone {
  if (action === "CLOSE_NO_FRAUD" || action === "ALLOW_TRANSACTION") return "success";
  if (action === "BLOCK_CARD" || action === "BLOCK_ALL_CARDS" || action === "FILE_REPORT") {
    return "danger";
  }
  return "neutral";
}

// ── the probability meter ────────────────────────────────────────────────────

/**
 * The four bands the meter uses, as token references.
 *
 * The thresholds are the policy's, not the designer's: 0.15 is where a case
 * may close, 0.70 is where an action becomes available. A reader who learns
 * the colours has learned the policy.
 */
export function meterVar(p: number): string {
  if (p <= 0.15) return "var(--meter-0)";
  if (p <= 0.4) return "var(--meter-1)";
  if (p <= 0.7) return "var(--meter-2)";
  return "var(--meter-3)";
}

export function meterTone(p: number): Tone {
  if (p <= 0.15) return "success";
  if (p <= 0.7) return "warning";
  return "danger";
}

// ── the evidence ledger's own vocabulary ─────────────────────────────────────

/**
 * Which glyph belongs to a ledger posting.
 *
 * Matched on the feature name the ledger posts under, because that is the
 * stable identifier — the claim text is written for a person and changes.
 * Anything unmatched gets the scope, which is the honest "we looked".
 */
export function featureIcon(feature: string): IconName {
  const f = feature.toLowerCase();
  if (f.includes("device")) return "device";
  if (f.includes("region") || f.includes("dist")) return "region";
  if (f.includes("recur") || f.includes("subscription")) return "recur";
  if (f.includes("window") || f.includes("velocity") || f.includes("burst")) return "window";
  if (f.includes("amount") || f.includes("p95") || f.includes("baseline")) return "baseline";
  if (f.includes("prior") || f.includes("case") || f.includes("memory")) return "memory";
  if (f.includes("policy") || f.includes("rule")) return "policy";
  if (f.includes("customer") || f.includes("response") || f.includes("denial")) return "ask";
  if (f.includes("card") || f.includes("ring") || f.includes("connected")) return "ring";
  return "scope";
}

/** A posting's direction, which decides the sign and the colour of its chip. */
export function postingTone(logLr: number): Tone {
  if (Math.abs(logLr) < 1e-6) return "neutral";
  return logLr > 0 ? "danger" : "success";
}

/**
 * A log-odds posting, signed.
 *
 * Rounded first, then signed. Signing first prints `-0.00` for a posting of
 * −0.004, which reads as a suppressed negative rather than as the nothing it
 * is.
 */
export function signed(value: number, digits = 2): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return (0).toFixed(digits);
  const fixed = rounded.toFixed(digits);
  return rounded > 0 ? `+${fixed}` : fixed;
}

// ── formatting ───────────────────────────────────────────────────────────────

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function humanise(value: string): string {
  return value.replace(/_/g, " ");
}

// ── the hunt strip ───────────────────────────────────────────────────────────

export interface HuntState {
  label: string;
  glyph: IconName;
  tone: Tone;
  note: string;
}

/**
 * The one-line state of the investigation, in the language of a search.
 *
 * It exists because a probability alone does not say whether the number is
 * still moving. The label answers "is the agent still working, and is it
 * getting warmer", and the note underneath says what it is doing right now or
 * why it stopped.
 */
export function huntState(
  running: boolean,
  p: number,
  verdict: Verdict | null,
  note: string,
): HuntState {
  if (running) {
    if (p > 0.6) return { label: "ON THE SCENT", glyph: "ring", tone: "danger", note };
    if (p < 0.25) return { label: "TRAIL COLD", glyph: "scope", tone: "success", note };
    return { label: "STALKING", glyph: "scope", tone: "accent", note };
  }
  if (verdict === "fraud") return { label: "QUARRY LOCKED", glyph: "bolt", tone: "danger", note };
  if (verdict === "legitimate") {
    return { label: "STOOD DOWN", glyph: "check", tone: "success", note };
  }
  if (verdict === "uncertain") {
    return { label: "HELD AT BAY", glyph: "alert", tone: "warning", note };
  }
  return { label: "NOT YET RUN", glyph: "scope", tone: "neutral", note };
}
