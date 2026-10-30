import type { EvidencePostedPayload } from "@/lib/generated/contract";
import { GROUP_CAP } from "@/lib/generated/contract";
import { RefChip } from "@/components/ui/RefChip";
import { EntityChip } from "@/components/ui/EntityChip";
import { SourceIcon } from "@/components/ui/SourceIcon";
import { lr as fmtLr } from "@/lib/format";
import { cn } from "@/lib/cn";

/**
 * One ledger posting: the claim, the query that produced it, the ids it rests
 * on, and how far it moved the probability.
 *
 * The hatched bar end matters — it is how an analyst sees that correlated
 * evidence was PREVENTED from double-counting, which is otherwise invisible.
 */
export function EvidenceItem({ posting }: { posting: EvidencePostedPayload }) {
  const magnitude = Math.min(Math.abs(posting.log_lr) / GROUP_CAP, 1);
  const towardFraud = posting.lr > 1.05;
  const neutral = posting.lr >= 0.95 && posting.lr <= 1.05;

  return (
    <li className="grid grid-cols-[16px_1fr_76px] items-start gap-2 border-b border-border py-2.5 last:border-0">
      <SourceIcon source={posting.source} className="mt-0.5" />

      <div className="min-w-0 space-y-1.5">
        <p className="text-small text-fg">{posting.claim}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <RefChip value={posting.ref} />
          {posting.entity_ids.map((id) => (
            <EntityChip key={id} id={id} />
          ))}
        </div>
      </div>

      <div className="flex flex-col items-end gap-1" title={`likelihood ratio ${fmtLr(posting.lr)}`}>
        <span className="font-mono text-mono-sm tabular-nums text-fg-muted">
          {neutral ? "–" : `${towardFraud ? "▲" : "▼"} ${fmtLr(posting.lr)}`}
        </span>
        <div className="h-1 w-16 overflow-hidden rounded-full bg-meter-track">
          <div
            className={cn(
              "h-full rounded-full",
              towardFraud ? "bg-danger/70 text-danger" : "bg-success/70 text-success",
              posting.capped && "bg-hatch",
            )}
            style={{ width: `${Math.max(magnitude * 100, 6)}%` }}
            title={posting.capped ? "Capped by group limit — correlated with evidence already counted" : undefined}
          />
        </div>
        {!posting.present && (
          <span className="text-[10px] leading-none text-fg-subtle" title="Absence is evidence: lr_absent was posted">
            absent
          </span>
        )}
      </div>
    </li>
  );
}
