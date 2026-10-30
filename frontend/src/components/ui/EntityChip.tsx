import { cn } from "@/lib/cn";

export type EntityKind = "card" | "txn" | "customer" | "case" | "device" | "region" | "unknown";

/** Mirrors the backend's id heuristics so the UI and the validator agree on
 *  what an id is: CC-#### closed case, *-K# card, all-digits transaction. */
export function classifyEntity(id: string): EntityKind {
  if (id.startsWith("CC-")) return "case";
  if (id.startsWith("CASE-")) return "case";
  if (/-K\d+$/.test(id)) return "card";
  if (/^\d+$/.test(id)) return "txn";
  if (/^C\d+$/.test(id)) return "customer";
  if (/^D[0-9a-f]{12}$/.test(id)) return "device";
  if (/^\d+\.\d$/.test(id)) return "region";
  return "unknown";
}

const GLYPH: Record<EntityKind, string> = {
  card: "▤",
  txn: "◇",
  customer: "○",
  case: "⌸",
  device: "⬢",
  region: "△",
  unknown: "·",
};

export function EntityChip({ id, className }: { id: string; className?: string }) {
  const kind = classifyEntity(id);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5",
        "font-mono text-mono-sm text-fg-muted",
        className,
      )}
      title={`${kind}: ${id}`}
    >
      <span className="text-fg-subtle" aria-hidden>{GLYPH[kind]}</span>
      {id}
    </span>
  );
}
