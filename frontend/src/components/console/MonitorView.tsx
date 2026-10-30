"use client";

/**
 * The whole pack at once, and whether it is defensible.
 *
 * Four ratios decide that, and three of them are ceilings rather than
 * targets. Half the benchmark is legitimate activity, so the documented
 * failure mode is not an agent that misses fraud — it is an agent that blocks
 * everything and scores well on recall. A block rate over 0.50, a SAR rate
 * over 0.20 or one verdict covering more than 60% of the pack each raise a
 * warning, and the warnings are shown at the top in red rather than at the
 * bottom in grey.
 *
 * The histogram matters as much as the ratios. A well-calibrated run is
 * bimodal with a populated middle; a run whose mass sits in one bar has
 * stopped discriminating whatever its accuracy says.
 */

import { Icon } from "@/components/console/icons";
import { ConsoleChip, Overline, StatTile } from "@/components/console/parts";
import { humanise, meterVar, pct, toneVar, verdictTone } from "@/components/console/vm";
import { useBenchmark } from "@/lib/hooks";
import type { BenchmarkReport } from "@/lib/generated/contract";

export function MonitorView() {
  const { data, isLoading, error } = useBenchmark();

  if (isLoading) {
    return <Frame><p className="text-caption text-fg-subtle">Scoring the pack…</p></Frame>;
  }
  if (error || !data) {
    return (
      <Frame>
        <p className="text-caption text-fg-subtle">
          Could not score the pack: {(error as Error | null)?.message ?? "no report"}
        </p>
      </Frame>
    );
  }

  const verdicts = Object.entries(data.verdict_mix).sort((a, b) => b[1] - a[1]);
  const dominant = verdicts[0];

  return (
    <div className="absolute inset-x-0 bottom-0 top-0 z-view overflow-y-auto bg-bg px-6 pb-9 pt-5">
      <header className="mb-4 flex flex-wrap items-end gap-3.5">
        <div>
          <div className="text-[16px] font-bold leading-tight">The pack, scored</div>
          <div className="mt-1.5 font-mono text-[10.5px] text-fg-muted">
            {data.valid} of {data.total} answer files valid · {data.elapsed_s.toFixed(0)}s wall
            clock
          </div>
        </div>
        <span className="flex-1" />
        <ConsoleChip tone={data.warnings.length === 0 ? "success" : "danger"}>
          {data.warnings.length === 0 ? "no warnings" : `${data.warnings.length} warnings`}
        </ConsoleChip>
      </header>

      {data.warnings.length > 0 ? (
        <ul className="mb-4 flex flex-col gap-1.5">
          {data.warnings.map((warning) => (
            <li
              key={warning}
              className="flex items-start gap-2.5 rounded-lg border border-danger bg-danger-tint px-3.5 py-2.5"
            >
              <Icon name="alert" size={14} className="mt-px shrink-0" style={{ color: "var(--danger)" }} />
              <span className="text-[12px] leading-snug text-danger">{warning}</span>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mb-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          label="Valid"
          value={`${data.valid}/${data.total}`}
          note="against the contract and the graph"
          tone={data.valid === data.total ? "success" : "danger"}
          icon="check"
        />
        <StatTile
          label="Block rate"
          value={pct(data.block_rate)}
          note="ceiling 50%"
          tone={data.block_rate > 0.5 ? "danger" : "success"}
          icon="bolt"
        />
        <StatTile
          label="SAR rate"
          value={pct(data.sar_rate)}
          note="ceiling 20%"
          tone={data.sar_rate > 0.2 ? "danger" : "success"}
          icon="doc"
        />
        <StatTile
          label="Changed"
          value={pct(data.changed_rate)}
          note="recommendation moved after evidence"
          tone={data.changed_rate < 0.3 ? "warning" : "success"}
          icon="recur"
        />
        <StatTile
          label="Dominant verdict"
          value={dominant ? `${dominant[1]} ${dominant[0]}` : "—"}
          note="ceiling 60% of the pack"
          tone={dominant && dominant[1] / Math.max(data.total, 1) > 0.6 ? "danger" : "success"}
          icon="scope"
        />
      </div>

      <Histogram report={data} />

      <section className="overflow-hidden rounded-lg border border-border bg-surface">
        <header className="flex items-center justify-between border-b border-border px-3.5 py-2.5">
          <Overline>Every case, as graded</Overline>
          <span className="font-mono text-[9px] text-fg-subtle">{data.cases.length} rows</span>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] border-collapse text-left">
            <thead>
              <tr className="border-b border-border bg-surface-2">
                {["Case", "Verdict", "P(fraud)", "Pattern", "Actions", "SAR", "Tools", "Tokens", "Elapsed"].map(
                  (head) => (
                    <th
                      key={head}
                      scope="col"
                      className="px-3 py-2 font-mono text-[9px] font-semibold uppercase tracking-[0.09em] text-fg-muted"
                    >
                      {head}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.cases.map((row) => (
                <tr key={row.case_id} className="border-b border-border last:border-b-0">
                  <td className="px-3 py-2.5 font-mono text-[10.5px] font-semibold">
                    {row.case_id}
                  </td>
                  <td className="px-3 py-2.5">
                    {row.verdict ? (
                      <ConsoleChip tone={verdictTone(row.verdict)}>{row.verdict}</ConsoleChip>
                    ) : (
                      <span className="text-caption text-fg-subtle">—</span>
                    )}
                  </td>
                  <td
                    className="px-3 py-2.5 font-mono text-[10.5px] font-bold tabular-nums"
                    style={{
                      color:
                        row.fraud_probability === null
                          ? "var(--fg-subtle)"
                          : meterVar(row.fraud_probability),
                    }}
                  >
                    {row.fraud_probability?.toFixed(4) ?? "—"}
                  </td>
                  <td className="px-3 py-2.5 text-[11.5px] text-fg-muted">
                    {row.pattern ? humanise(row.pattern) : "—"}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[10px] text-fg-muted">
                    {row.actions.join(", ") || "—"}
                  </td>
                  <td className="px-3 py-2.5">
                    {row.sar ? <ConsoleChip tone="danger">filed</ConsoleChip> : null}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[10.5px] tabular-nums text-fg-muted">
                    {row.tool_calls}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[10.5px] tabular-nums text-fg-muted">
                    {row.tokens.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[10.5px] tabular-nums text-fg-muted">
                    {row.elapsed_s.toFixed(1)}s
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/** The tallest bar, in pixels. Everything else is scaled against it. */
const BAR_H = 104;

function Histogram({ report }: { report: BenchmarkReport }) {
  const bins = report.probability_histogram;
  const peak = Math.max(1, ...bins.map((bin) => bin.count));
  return (
    <section className="mb-4 rounded-lg border border-border bg-surface px-4 pb-3 pt-3.5">
      <header className="mb-2.5 flex items-center justify-between">
        <Overline>Probability distribution — bars past 0.85 are where an action opens</Overline>
        <span className="font-mono text-[9.5px] text-fg-subtle">{bins.length} bins</span>
      </header>
      <div className="flex items-end gap-1.5">
        {bins.map((bin) => {
          const mid = (bin.lower + bin.upper) / 2;
          // Pixels, not percent. A percentage height inside a flex column
          // whose parent has no definite height resolves to zero, which is
          // how a bar chart ships with no bars in it.
          const height = bin.count === 0 ? 2 : Math.max(6, (bin.count / peak) * BAR_H);
          return (
            <div key={bin.lower} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
              <span className="font-mono text-[9px] font-semibold tabular-nums text-fg-muted">
                {bin.count || ""}
              </span>
              <div
                className="w-full rounded-t-sm"
                style={{
                  height: `${height}px`,
                  background: bin.count > 0 ? meterVar(mid) : "var(--meter-track)",
                  transition: "height var(--motion-slow) var(--ease-standard)",
                }}
              />
              <span className="font-mono text-[8.5px] tabular-nums text-fg-subtle">
                {bin.lower.toFixed(1)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="mt-2.5 flex gap-3">
        {Object.entries(report.verdict_mix).map(([verdict, count]) => (
          <span key={verdict} className="flex items-center gap-1.5 font-mono text-[9px] text-fg-muted">
            <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
              <rect
                width="8"
                height="8"
                rx="2"
                fill={toneVar(verdictTone(verdict as "fraud" | "legitimate" | "uncertain"))}
              />
            </svg>
            {count} {verdict}
          </span>
        ))}
      </div>
    </section>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-x-0 bottom-0 top-0 z-view flex items-center justify-center bg-bg p-8">
      {children}
    </div>
  );
}
