import type { EvidenceSource } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

const CONFIG: Record<EvidenceSource, { glyph: string; tone: string; label: string }> = {
  graph: { glyph: "⬡", tone: "text-fg-muted", label: "from the graph" },
  /** GraphRAG's visible footprint — the only accent-coloured source. */
  document: { glyph: "☷", tone: "text-accent", label: "from a policy or regulatory document" },
  customer: { glyph: "◗", tone: "text-warning", label: "from the customer" },
  external: { glyph: "⊕", tone: "text-fg-subtle", label: "from an external source" },
};

export function SourceIcon({ source, className }: { source: EvidenceSource; className?: string }) {
  const c = CONFIG[source];
  return (
    <span className={cn("text-caption leading-none", c.tone, className)} title={c.label}>
      <span aria-hidden>{c.glyph}</span>
      <span className="sr-only">{c.label}</span>
    </span>
  );
}
