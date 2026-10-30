"use client";

/**
 * The twenty cases, ranked by what deserves attention.
 *
 * Ordered by exposure × uncertainty rather than by probability. A case at 0.97
 * has already been decided; a case at 0.55 with four thousand dollars behind
 * it is the one an analyst should open next. Sorting by probability would put
 * the settled cases on top and bury the work.
 *
 * Each row carries its own dial, so the rail is scannable without reading a
 * single number — and a case still running shows a moving dial rather than a
 * spinner, because the value is the progress.
 */

import { Icon } from "@/components/console/icons";
import { ConsoleChip, Dial, Overline } from "@/components/console/parts";
import { money, triggerIcon, triggerTone, toneVar, verdictTone } from "@/components/console/vm";
import type { CaseSummary } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

/**
 * What should be looked at next.
 *
 * Uncertainty peaks at p = 0.5 and falls to nothing at either end, so
 * `p(1-p)` is the shape. Exposure is damped by a log because the difference
 * between $30 and $300 matters and the difference between $3,000 and $30,000
 * matters much less once it is already going to a human either way.
 */
export function attentionScore(row: CaseSummary): number {
  const p = row.fraud_probability ?? 0.5;
  const uncertainty = p * (1 - p);
  const exposure = Math.log10(1 + Math.max(row.exposure_usd ?? 0, 0));
  return uncertainty * (1 + exposure);
}

export function QueueRail({
  rows,
  selected,
  runningCase,
  onSelect,
}: {
  rows: CaseSummary[];
  selected: string;
  runningCase: string | null;
  onSelect: (caseId: string) => void;
}) {
  const ordered = [...rows].sort((a, b) => {
    const delta = attentionScore(b) - attentionScore(a);
    return Math.abs(delta) > 1e-9 ? delta : a.case_id.localeCompare(b.case_id);
  });

  return (
    <aside className="flex min-h-0 flex-col border-r border-border bg-surface">
      <div className="flex shrink-0 items-baseline justify-between px-3.5 pb-2 pt-3">
        <Overline className="tracking-[0.14em]">Case queue</Overline>
        <span className="font-mono text-[9px] font-medium text-fg-subtle">
          exposure × uncertainty
        </span>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {ordered.map((row) => {
          const on = row.case_id === selected;
          const live = row.case_id === runningCase;
          const tone = live ? "accent" : verdictTone(row.verdict);
          return (
            <li key={row.case_id}>
              <button
                type="button"
                onClick={() => onSelect(row.case_id)}
                aria-current={on ? "true" : undefined}
                className={cn(
                  "mb-1 w-full rounded-lg border px-2.5 py-2.5 text-left transition-colors duration-fast",
                  on
                    ? "border-accent bg-accent-tint"
                    : "border-transparent hover:bg-surface-2",
                )}
              >
                <div className="flex items-center gap-1.5">
                  <Icon
                    name={triggerIcon(row.trigger_type)}
                    size={13}
                    style={{ color: toneVar(triggerTone(row.trigger_type)) }}
                  />
                  <span className="whitespace-nowrap font-mono text-[11px] font-semibold tracking-[0.02em]">
                    {row.case_id}
                  </span>
                  <span className="flex-1" />
                  <Dial
                    p={row.has_answer ? row.fraud_probability : null}
                    size={26}
                    stroke={3}
                    muted={!row.has_answer}
                  />
                </div>
                <div className="mt-1.5 flex items-center gap-1.5">
                  <ConsoleChip tone={tone}>
                    {live ? "investigating" : (row.verdict ?? "not run")}
                  </ConsoleChip>
                  <span className="font-mono text-[10px] font-medium tabular-nums text-fg-muted">
                    {money(row.exposure_usd)}
                  </span>
                  <span className="flex-1" />
                  <span
                    className="font-mono text-[11px] font-bold tabular-nums"
                    style={{ color: toneVar(tone) }}
                  >
                    {row.has_answer && row.fraud_probability !== null
                      ? row.fraud_probability.toFixed(2)
                      : "—"}
                  </span>
                </div>
              </button>
            </li>
          );
        })}
        {ordered.length === 0 ? (
          <li className="px-2 py-8 text-center text-caption text-fg-subtle">No cases.</li>
        ) : null}
      </ul>
    </aside>
  );
}
