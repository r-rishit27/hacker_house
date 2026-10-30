import { probability } from "@/lib/format";
import { cn } from "@/lib/cn";

/** Fill stops from DESIGN_SYSTEM.md §2.3. Identical in both themes, so a
 *  screenshot of the meter means the same thing whichever theme it came from. */
function fillClass(p: number): string {
  if (p <= 0.15) return "bg-meter-0";
  if (p <= 0.4) return "bg-meter-1";
  if (p <= 0.7) return "bg-meter-2";
  return "bg-meter-3";
}

export interface ProbabilityMeterProps {
  value: number;
  /** The ledger prior — shown as a caret, because "this started at 0.25 because
   *  it was a score trigger" is one of the most important things to say. */
  prior?: number;
  verdictLabel?: string;
  className?: string;
}

export function ProbabilityMeter({ value, prior, verdictLabel, className }: ProbabilityMeterProps) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="relative min-w-24 flex-1">
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-meter-track"
          role="meter"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuetext={verdictLabel ? `${probability(value)}, ${verdictLabel}` : probability(value)}
        >
          <div className={cn("h-full rounded-full transition-[width] duration-base ease-standard", fillClass(value))} style={{ width: `${pct}%` }} />
        </div>
        {/* 0.50 tick */}
        <span className="pointer-events-none absolute left-1/2 top-0 h-1.5 w-px bg-fg-subtle/60" aria-hidden />
        {prior !== undefined && (
          <span
            className="pointer-events-none absolute -bottom-1.5 -translate-x-1/2 text-[8px] leading-none text-fg-subtle"
            style={{ left: `${Math.max(0, Math.min(1, prior)) * 100}%` }}
            title={`prior ${probability(prior)}`}
            aria-hidden
          >
            {"▲"}
          </span>
        )}
      </div>
      {/* The numeral is never omitted. Colour accelerates recognition; it is not the value. */}
      <span className="font-mono text-mono tabular-nums text-fg">{probability(value)}</span>
    </div>
  );
}
