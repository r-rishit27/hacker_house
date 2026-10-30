import type { Verdict } from "@/lib/generated/contract";
import { Chip } from "./Chip";

/** Never colour alone: icon + word + colour. DESIGN_SYSTEM.md §3.1. */
const GLYPH: Record<Verdict, string> = {
  fraud: "⬣",       // filled octagon
  legitimate: "✓",  // check
  uncertain: "◑",   // half-filled circle
};

const TONE = { fraud: "danger", legitimate: "success", uncertain: "warning" } as const;

const LABEL: Record<Verdict, string> = {
  fraud: "fraud",
  legitimate: "legitimate",
  /** Uncertain is a valid, respectable outcome here — not a warning state. */
  uncertain: "uncertain",
};

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  return (
    <Chip tone={TONE[verdict]} fill="tint">
      <span aria-hidden>{GLYPH[verdict]}</span>
      {LABEL[verdict]}
    </Chip>
  );
}
