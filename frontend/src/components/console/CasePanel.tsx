"use client";

/**
 * One case: its header, and the five ways of reading it.
 *
 * The tabs are ordered by how much interpretation each one carries, least
 * first. The timeline is what happened; the case file is what was concluded;
 * the SAR is what gets filed; memory is what it was compared against; and the
 * answer file is the bytes a judge is actually graded on. A reader who
 * distrusts the prose can walk right and end at the JSON.
 */

import { useEffect, useMemo, useState } from "react";

import { Icon, type IconName } from "@/components/console/icons";
import { ConsoleChip, Dial, Overline } from "@/components/console/parts";
import { Timeline } from "@/components/console/Timeline";
import {
  humanise,
  money,
  sourceIcon,
  sourceTone,
  toneTint,
  toneVar,
  verdictTone,
} from "@/components/console/vm";
import {
  useCase,
  useMemoryTab,
  useSar,
  useStartInvestigation,
  useTrace,
} from "@/lib/hooks";
import { useReplay } from "@/lib/replay";
import type { CaseDetail, MemoryBody, SarBody, TraceBody } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

export type TabId = "timeline" | "case" | "sar" | "memory" | "json";

const TABS: { id: TabId; label: string; icon: IconName }[] = [
  { id: "timeline", label: "Timeline", icon: "window" },
  { id: "case", label: "Case file", icon: "doc" },
  { id: "sar", label: "SAR", icon: "alert" },
  { id: "memory", label: "Memory", icon: "memory" },
  { id: "json", label: "Answer file", icon: "doc" },
];

export interface CasePanelState {
  running: boolean;
  probability: number | null;
  note: string;
  progress: number;
}

export function CasePanel({
  caseId,
  liveRunId,
  onState,
  onStart,
}: {
  caseId: string;
  liveRunId: string | null;
  onState: (state: CasePanelState) => void;
  onStart: (runId: string) => void;
}) {
  const [tab, setTab] = useState<TabId>("timeline");
  const detail = useCase(caseId);
  const trace = useTrace(caseId, true);
  const start = useStartInvestigation();

  // Replay only a recorded run. While a run is genuinely streaming the events
  // are already paced by the server and re-pacing them would be a lie.
  const postings = useMemo(() => trace.data?.postings ?? [], [trace.data]);
  const replay = useReplay(postings, liveRunId === null);

  const answer = detail.data?.answer ?? null;
  const verdict = answer?.case.verdict ?? null;
  const settled = answer?.case.fraud_probability ?? null;
  const head = replay.shown.at(-1);
  const current = replay.running ? (head?.p_after ?? null) : settled;

  useEffect(() => {
    onState({
      running: replay.running,
      probability: current,
      note: replay.running
        ? `posting ${replay.revealed} of ${replay.total}${head ? ` · ${humanise(head.feature)}` : ""}`
        : (answer?.stop_reason?.split(/\.\s/)[0] ?? "").concat(answer?.stop_reason ? "." : ""),
      progress: replay.total === 0 ? 0 : replay.revealed / replay.total,
    });
    // `onState` is a setter from the shell and stable enough; including it
    // would loop, since setting state re-renders the shell.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replay.running, replay.revealed, replay.total, current, answer, head]);

  useEffect(() => {
    setTab("timeline");
  }, [caseId]);

  if (detail.error) {
    return (
      <main className="flex min-w-0 items-center justify-center p-8 text-caption text-fg-subtle">
        Could not load {caseId}: {(detail.error as Error).message}
      </main>
    );
  }
  if (detail.isLoading || !detail.data) {
    return (
      <main className="flex min-w-0 items-center justify-center p-8 text-caption text-fg-subtle">
        Loading {caseId}…
      </main>
    );
  }

  const { alert } = detail.data;
  const tone = replay.running ? "accent" : verdictTone(verdict);

  const counts: Record<TabId, number> = {
    timeline: postings.length,
    case: answer?.case.evidence.length ?? 0,
    sar: answer?.sar.file ? 1 : 0,
    memory: answer?.case.similar_prior_cases.length ?? 0,
    json: answer ? 1 : 0,
  };

  return (
    <main className="flex min-h-0 min-w-0 flex-col bg-bg">
      <header className="shrink-0 border-b border-border px-5 pt-3.5">
        <div className="flex items-start gap-3.5">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="whitespace-nowrap text-[17px] font-bold leading-tight">
                {alert.alert_id}
              </h1>
              <ConsoleChip>{humanise(alert.trigger_type)}</ConsoleChip>
              <ConsoleChip tone={tone}>
                {replay.running ? "investigating" : (verdict ?? "not run")}
              </ConsoleChip>
              {answer?.case.pattern && answer.case.pattern !== "none" ? (
                <ConsoleChip tone="info">{humanise(answer.case.pattern)}</ConsoleChip>
              ) : null}
              {trace.data ? (
                <span className="font-mono text-[9px] text-fg-subtle">
                  replay of {trace.data.run_id}
                </span>
              ) : null}
            </div>
            <p className="mt-1.5 max-w-[70ch] text-[12px] leading-relaxed text-fg-muted">
              {alert.trigger_text}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-3.5 pt-0.5">
            {replay.running ? (
              <button
                type="button"
                onClick={replay.finish}
                className="rounded-md border border-border-strong bg-surface px-2.5 py-1.5 font-mono text-[9.5px] font-semibold text-fg-muted hover:text-fg"
              >
                skip replay
              </button>
            ) : (
              <button
                type="button"
                onClick={() =>
                  postings.length > 0
                    ? replay.restart()
                    : start.mutate(caseId, { onSuccess: (a) => onStart(a.run_id) })
                }
                className="rounded-md border border-border-strong bg-surface px-2.5 py-1.5 font-mono text-[9.5px] font-semibold text-fg-muted hover:text-fg"
              >
                {postings.length > 0 ? "replay" : "investigate"}
              </button>
            )}
            <div className="text-right">
              <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-fg-muted">
                Exposure
              </div>
              <div className="mt-1 font-mono text-[17px] font-bold tabular-nums">
                {money(answer?.case.exposure_usd ?? null)}
              </div>
            </div>
            <Dial p={current} size={62} label="P(fraud)" />
          </div>
        </div>

        <div
          role="tablist"
          aria-label="Case views"
          className="mt-3 flex min-w-0 items-center gap-0.5 overflow-x-auto"
        >
          {TABS.map((item) => {
            const on = tab === item.id;
            return (
              <button
                key={item.id}
                role="tab"
                aria-selected={on}
                type="button"
                onClick={() => setTab(item.id)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3.5 py-2.5",
                  "text-[11.5px] font-semibold",
                  on
                    ? "border-accent text-fg"
                    : "border-transparent text-fg-muted hover:text-fg",
                )}
              >
                <Icon name={item.icon} size={13} />
                {item.label}
                <span
                  className={cn(
                    "rounded-sm px-1.5 font-mono text-[9px] font-semibold leading-[1.5]",
                    on ? "bg-accent-tint text-accent" : "bg-surface-3 text-fg-subtle",
                  )}
                >
                  {counts[item.id]}
                </span>
              </button>
            );
          })}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        {tab === "timeline" ? (
          <Timeline
            postings={postings}
            trajectory={trace.data?.trajectory ?? []}
            revealed={replay.revealed}
            running={replay.running}
            stopReason={answer?.stop_reason ?? ""}
          />
        ) : null}
        {tab === "case" ? <CaseFile detail={detail.data} trace={trace.data ?? null} /> : null}
        {tab === "sar" ? <SarTab caseId={caseId} /> : null}
        {tab === "memory" ? <MemoryTab caseId={caseId} /> : null}
        {tab === "json" ? <AnswerFileTab detail={detail.data} /> : null}
      </div>
    </main>
  );
}

// ── the case file ────────────────────────────────────────────────────────────

function CaseFile({ detail, trace }: { detail: CaseDetail; trace: TraceBody | null }) {
  const answer = detail.answer;
  if (!answer) {
    return <p className="text-caption text-fg-subtle">This case has no answer file yet.</p>;
  }
  const body = answer.case;
  const facts = [
    { k: "Verdict", v: body.verdict, tone: verdictTone(body.verdict) },
    { k: "Pattern", v: humanise(body.pattern), tone: "neutral" as const },
    {
      k: "Exposure",
      v: money(body.exposure_usd),
      tone: body.exposure_usd > 2500 ? ("danger" as const) : ("neutral" as const),
    },
    {
      k: "Graph write",
      v: body.written_to_graph ? body.graph_case_id : "not written",
      tone: body.written_to_graph ? ("success" as const) : ("neutral" as const),
    },
  ];

  return (
    <div className="flex flex-col gap-3.5">
      <section className="rounded-lg border border-border bg-surface p-3.5">
        <Overline>Summary</Overline>
        <p className="mt-2 text-[13px] leading-relaxed text-fg">{body.summary}</p>
        <div className="mt-3.5 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-4">
          {facts.map((fact) => (
            <div key={fact.k} className="bg-surface px-3 py-2.5">
              <div className="font-mono text-[9px] uppercase tracking-[0.08em] text-fg-muted">
                {fact.k}
              </div>
              <div
                className="mt-1.5 font-mono text-[14px] font-bold leading-tight"
                style={{ color: toneVar(fact.tone) }}
              >
                {fact.v}
              </div>
            </div>
          ))}
        </div>
        {trace ? (
          <p className="mt-3 font-mono text-[9.5px] text-fg-subtle">
            {trace.tool_calls} tool calls · {trace.tokens.toLocaleString()} tokens ·{" "}
            {trace.latency_s.toFixed(1)}s
          </p>
        ) : null}
      </section>

      <section className="overflow-hidden rounded-lg border border-border bg-surface">
        <header className="flex items-center justify-between border-b border-border px-3.5 py-2.5">
          <Overline>Evidence ledger</Overline>
          <span className="font-mono text-[9px] text-fg-subtle">every row re-runnable</span>
        </header>
        <ul>
          {body.evidence.map((item, index) => {
            const tone = sourceTone(item.source);
            return (
              <li
                key={`${item.ref}-${index}`}
                className="flex gap-2.5 border-b border-border px-3.5 py-3 last:border-b-0"
              >
                <Icon
                  name={sourceIcon(item.source)}
                  size={14}
                  className="mt-0.5 shrink-0"
                  style={{ color: toneVar(tone) }}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-[12.5px] leading-relaxed text-fg">{item.claim}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <code
                      className="rounded-sm px-1.5 py-0.5 font-mono text-[10px] leading-relaxed"
                      style={{ background: toneTint(tone), color: toneVar(tone) }}
                    >
                      {item.ref}
                    </code>
                    {item.entity_ids.map((id) => (
                      <code
                        key={id}
                        className="rounded-sm bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] leading-relaxed text-fg-muted"
                      >
                        {id}
                      </code>
                    ))}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

// ── SAR ──────────────────────────────────────────────────────────────────────

function SarTab({ caseId }: { caseId: string }) {
  const { data, isLoading, error } = useSar(caseId, true);
  if (isLoading) return <p className="text-caption text-fg-subtle">Loading…</p>;
  if (error || !data) {
    return <p className="text-caption text-fg-subtle">No report for this case.</p>;
  }
  return <SarBodyView body={data} />;
}

function SarBodyView({ body }: { body: SarBody }) {
  const sar = body.sar;
  if (!sar.file) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-border bg-surface p-4">
        <Icon name="check" size={18} className="mt-px shrink-0" style={{ color: "var(--success)" }} />
        <div>
          <div className="text-[13px] font-semibold">No report filed</div>
          <p className="mt-1.5 max-w-[64ch] text-[12.5px] leading-relaxed text-fg-muted">
            {sar.reason}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <header className="flex items-center gap-2.5 border-b border-border bg-surface-2 px-4 py-3">
        <Icon name="doc" size={16} style={{ color: "var(--warning)" }} />
        <span className="text-[12px] font-bold tracking-[0.06em]">
          SUSPICIOUS ACTIVITY REPORT
        </span>
        <span className="flex-1" />
        <ConsoleChip tone="danger" fill="solid">
          L2 required
        </ConsoleChip>
      </header>
      <div className="grid grid-cols-1 gap-px bg-border sm:grid-cols-3">
        {[
          { k: "Total amount", v: money(sar.total_amount_usd) },
          {
            k: "Activity dates",
            v: sar.activity_dates.length > 0 ? sar.activity_dates.join(" → ") : "—",
          },
          { k: "Subjects", v: `${sar.subjects.length} entities` },
        ].map((meta) => (
          <div key={meta.k} className="bg-surface px-3.5 py-3">
            <div className="font-mono text-[9px] uppercase tracking-[0.08em] text-fg-muted">
              {meta.k}
            </div>
            <div className="mt-1.5 font-mono text-[12.5px] font-semibold leading-tight">
              {meta.v}
            </div>
          </div>
        ))}
      </div>
      <div className="p-4">
        <div className="mb-2 font-mono text-[9px] uppercase tracking-[0.1em] text-fg-muted">
          Narrative — who · what · when · where · how · why
        </div>
        <p className="text-[13px] leading-[1.75] text-fg">{sar.narrative}</p>
        <div className="mt-3.5 flex flex-wrap gap-1.5">
          {sar.subjects.map((subject) => (
            <code
              key={subject}
              className="rounded-sm px-2 py-0.5 font-mono text-[10px] leading-relaxed"
              style={{ background: "var(--info-tint)", color: "var(--info)" }}
            >
              {subject}
            </code>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── memory ───────────────────────────────────────────────────────────────────

function MemoryTab({ caseId }: { caseId: string }) {
  const { data, isLoading, error } = useMemoryTab(caseId, true);
  if (isLoading) return <p className="text-caption text-fg-subtle">Loading…</p>;
  if (error || !data) {
    return <p className="text-caption text-fg-subtle">No retrieval recorded for this case.</p>;
  }
  return <MemoryBodyView body={data} />;
}

function MemoryBodyView({ body }: { body: MemoryBody }) {
  const cited = new Set(body.cited);
  const hits = [...body.prior_closed_cases, ...body.sentinel_cases];
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <Overline>Retrieved memory</Overline>
        <span className="font-mono text-[9px] text-fg-subtle">
          vector + structural, fused by RRF
        </span>
      </div>
      {hits.map((hit) => {
        const isCited = cited.has(hit.id);
        const tone = isCited ? "info" : "neutral";
        return (
          <div
            key={hit.id}
            className="flex items-center gap-3 rounded-lg border bg-surface px-3.5 py-3"
            style={{ borderColor: isCited ? toneVar("info") : "var(--border)" }}
          >
            <Icon
              name={hit.kind === "sentinel_case" ? "hex" : "memory"}
              size={16}
              className="shrink-0"
              style={{ color: toneVar(tone) }}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11.5px] font-semibold">{hit.id}</span>
                <ConsoleChip tone={tone}>{humanise(hit.kind)}</ConsoleChip>
                {isCited ? <ConsoleChip tone="accent">cited</ConsoleChip> : null}
              </div>
              <p className="mt-1.5 text-[12px] leading-snug text-fg-muted">
                {hit.snippet || hit.title}
              </p>
            </div>
            <div className="w-[92px] shrink-0">
              <div className="text-right font-mono text-[10px] font-semibold tabular-nums text-fg-muted">
                {hit.score.toFixed(3)}
              </div>
              <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-meter-track">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${Math.min(Math.max(hit.score, 0), 1) * 100}%`,
                    background: toneVar(tone),
                  }}
                />
              </div>
            </div>
          </div>
        );
      })}
      {hits.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3.5 py-6 text-center text-caption text-fg-subtle">
          This run retrieved nothing.
        </p>
      ) : null}
    </div>
  );
}

// ── the graded bytes ─────────────────────────────────────────────────────────

function AnswerFileTab({ detail }: { detail: CaseDetail }) {
  const answer = detail.answer;
  const validation = detail.validation;
  if (!answer) {
    return <p className="text-caption text-fg-subtle">No answer file on disk for this case.</p>;
  }
  const ok = validation?.ok ?? false;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <header className="flex flex-wrap items-center gap-2.5 border-b border-border bg-surface-2 px-3.5 py-2.5">
        <Icon name="doc" size={14} style={{ color: "var(--info)" }} />
        <span className="font-mono text-[11px] font-semibold">cases/{answer.case_id}.json</span>
        <ConsoleChip tone={ok ? "success" : "danger"}>
          {ok ? "schema-valid" : `${validation?.errors.length ?? 0} errors`}
        </ConsoleChip>
        <span className="flex-1" />
        <span className="font-mono text-[9.5px] text-fg-subtle">
          every id verified against the graph before write
        </span>
      </header>
      {validation && !ok ? (
        <ul className="border-b border-border bg-danger-tint px-3.5 py-2.5">
          {validation.errors.map((finding, index) => (
            <li key={index} className="font-mono text-[10.5px] text-danger">
              {finding.path}: {finding.message}
            </li>
          ))}
        </ul>
      ) : null}
      <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-words px-4 py-4 font-mono text-[11.5px] leading-[1.65] text-fg">
        {JSON.stringify(answer, null, 2)}
      </pre>
    </div>
  );
}
