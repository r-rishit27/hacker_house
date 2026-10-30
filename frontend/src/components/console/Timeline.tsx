"use client";

/**
 * The investigation, posting by posting.
 *
 * This is the screen the whole system is for. Everything else states a
 * conclusion; this shows the argument that produced it — which question was
 * asked, what came back, which way it moved the number and by how much.
 *
 * Three things are deliberate.
 *
 * **The log-odds are shown, signed, per row.** A posting that moved the
 * probability from 0.81 to 0.07 did so by a specific amount against a fitted
 * likelihood ratio, and hiding that leaves a reader with nothing to argue
 * with. A row clipped by its group cap says so, because the number posted and
 * the number applied then differ and that is exactly when someone should look.
 *
 * **Exonerating evidence is as loud as incriminating evidence.** Green rows
 * get the same weight, the same chip, the same width as red ones. A console
 * that whispers the case for innocence is a console that convicts.
 *
 * **The order is preserved and paced.** See lib/replay.ts for why a finished
 * run still arrives one row at a time, and why that is a replay rather than a
 * pretence of live work.
 */

import { useMemo } from "react";

import { Icon } from "@/components/console/icons";
import { ConsoleChip, Overline } from "@/components/console/parts";
import {
  featureIcon,
  humanise,
  meterVar,
  postingTone,
  signed,
  sourceTone,
  toneTint,
  toneVar,
} from "@/components/console/vm";
import type { PostingView } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

const VIEW_W = 620;
const VIEW_H = 84;
const PAD_X = 18;
/** y for p = 0.85, the line above which an action becomes available. */
const Y_ACT = 21;
/** y for p = 0.15, the line below which a case may close. */
const Y_CLEAR = 63;

/** Map a probability to a y, with 0.85 and 0.15 landing on the guide lines. */
function yFor(p: number): number {
  const span = (Y_CLEAR - Y_ACT) / (0.85 - 0.15);
  return Y_CLEAR - (p - 0.15) * span;
}

export interface TimelineProps {
  postings: PostingView[];
  /** The whole recorded trajectory, including the prior the run opened at. */
  trajectory: number[];
  revealed: number;
  running: boolean;
  /** Why the agent stopped. Shown once the last posting has landed. */
  stopReason: string;
}

export function Timeline({
  postings,
  trajectory,
  revealed,
  running,
  stopReason,
}: TimelineProps) {
  const shown = postings.slice(0, revealed);

  // The trajectory carries the opening prior in front of the postings, so a
  // reveal of n postings is a line of n+1 points. Slicing both from the same
  // count is what keeps the chart and the rows in step.
  const points = useMemo(() => {
    const values = trajectory.slice(0, revealed + 1);
    const last = Math.max(values.length - 1, 1);
    return values.map((p, index) => ({
      x: PAD_X + (index / Math.max(last, 1)) * (VIEW_W - PAD_X * 2),
      y: yFor(p),
      p,
    }));
  }, [trajectory, revealed]);

  const head = points.at(-1);
  const polyline = points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");

  // Where the agent stopped to ask the customer something. The one moment in
  // the run where the next number depends on a person rather than the graph.
  const askAt = postings.findIndex((posting) => posting.source === "customer");
  const askX =
    askAt >= 0 && askAt < revealed && points.length > 1
      ? PAD_X + ((askAt + 1) / Math.max(points.length - 1, 1)) * (VIEW_W - PAD_X * 2)
      : null;

  return (
    <div>
      <section className="mb-3.5 rounded-lg border border-border bg-surface px-3.5 pb-2.5 pt-3">
        <header className="mb-2 flex items-center justify-between">
          <Overline>Probability trajectory</Overline>
          <span className="font-mono text-[10px] font-medium tabular-nums text-fg-muted">
            {revealed === 0
              ? "no postings"
              : `posting ${Math.min(revealed, postings.length)} of ${postings.length}`}
          </span>
        </header>
        <svg
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          className="block h-[84px] w-full"
          role="img"
          aria-label={
            head
              ? `Probability of fraud ${head.p.toFixed(2)} after ${revealed} postings`
              : "No postings yet"
          }
        >
          <line
            x1="0"
            y1={Y_ACT}
            x2={VIEW_W}
            y2={Y_ACT}
            stroke="var(--meter-track)"
            strokeWidth="1"
            strokeDasharray="4 5"
          />
          <line
            x1="0"
            y1={Y_CLEAR}
            x2={VIEW_W}
            y2={Y_CLEAR}
            stroke="var(--meter-track)"
            strokeWidth="1"
            strokeDasharray="4 5"
          />
          <text x="4" y={Y_ACT - 4} fill="var(--fg-subtle)" fontFamily="var(--font-mono)" fontSize="9.5">
            0.85 act
          </text>
          <text
            x="4"
            y={Y_CLEAR + 12}
            fill="var(--fg-subtle)"
            fontFamily="var(--font-mono)"
            fontSize="9.5"
          >
            0.15 clear
          </text>

          {askX !== null ? (
            <line
              x1={askX}
              y1="6"
              x2={askX}
              y2="78"
              stroke="var(--warning)"
              strokeWidth="1.4"
              strokeDasharray="3 3"
            />
          ) : null}

          {points.length > 1 ? (
            <polyline
              points={polyline}
              fill="none"
              stroke={head ? meterVar(head.p) : "var(--border-strong)"}
              strokeWidth="2.2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ) : null}

          {points.map((point, index) => {
            const isHead = index === points.length - 1;
            return (
              <circle
                key={index}
                cx={point.x}
                cy={point.y}
                r={isHead ? 4.5 : 3}
                fill={isHead ? meterVar(point.p) : "var(--border-strong)"}
                stroke="var(--surface)"
                strokeWidth="1.5"
              />
            );
          })}
        </svg>
      </section>

      <ol className="flex flex-col gap-[7px]">
        {shown.map((posting, index) => (
          <PostingRow
            key={`${posting.feature}-${index}`}
            posting={posting}
            live={running && index === shown.length - 1}
          />
        ))}
      </ol>

      {running ? (
        <div className="animate-s-pulse mt-[7px] flex items-center gap-2.5 rounded-lg border border-dashed border-border-strong px-3.5 py-3 font-mono text-[11px] font-medium text-fg-muted">
          <span className="h-[7px] w-[7px] rounded-full bg-accent-bold" />
          investigating…
        </div>
      ) : null}

      {!running && stopReason ? (
        <div className="animate-s-in mt-[7px] flex items-start gap-2.5 rounded-lg border border-border bg-surface px-3.5 py-3">
          <Icon name="check" size={16} className="mt-px shrink-0" style={{ color: "var(--success)" }} />
          <div>
            <Overline>Stop condition</Overline>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg">{stopReason}</p>
          </div>
        </div>
      ) : null}

      {!running && shown.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3.5 py-6 text-center text-caption text-fg-subtle">
          This run posted no evidence.
        </p>
      ) : null}
    </div>
  );
}

function PostingRow({ posting, live }: { posting: PostingView; live: boolean }) {
  const tone = postingTone(posting.log_lr);
  const src = sourceTone(posting.source);
  const flat = Math.abs(posting.log_lr) < 1e-6;

  return (
    <li
      className={cn(
        "animate-s-in flex items-start gap-2.5 rounded-lg border bg-surface px-3.5 py-3",
        live ? "bg-working" : "",
      )}
      style={{ borderColor: live ? "var(--accent-bold)" : "var(--border)" }}
    >
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
        style={{ background: toneTint(src), color: toneVar(src) }}
      >
        <Icon name={featureIcon(posting.feature)} size={16} />
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10.5px] font-semibold text-accent">
            {humanise(posting.feature)}
          </span>
          <ConsoleChip tone={src}>{posting.source}</ConsoleChip>
          {posting.capped ? (
            <ConsoleChip tone="neutral">
              capped · {humanise(posting.group)}
            </ConsoleChip>
          ) : null}
          {!posting.present ? <ConsoleChip tone="neutral">absent</ConsoleChip> : null}
        </div>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg">{posting.claim}</p>
        {posting.ref ? (
          <code className="mt-2 inline-block rounded-sm border border-accent-tint bg-accent-tint px-1.5 py-0.5 font-mono text-[10px] leading-relaxed text-accent">
            {posting.ref}
          </code>
        ) : null}
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1.5 pl-1.5">
        <ConsoleChip tone={tone}>{flat ? "—" : `${signed(posting.log_lr)} logLR`}</ConsoleChip>
        <span className="font-mono text-[10px] font-medium tabular-nums text-fg-muted">
          {posting.p_before.toFixed(2)} →{" "}
          <b style={{ color: meterVar(posting.p_after) }}>{posting.p_after.toFixed(2)}</b>
        </span>
      </div>
    </li>
  );
}
