"use client";

/**
 * The console: one screen, three views, no navigation.
 *
 * An analyst working a case does not want to be moved between pages. The
 * queue, the case and the graph are three answers to three different
 * questions and they are held open at once, because the work is moving
 * between them rather than through them. Below 1180px the graph pane folds
 * away first — it is the one the other two can be read without.
 *
 * The masthead's moving rule is the only continuous animation on the page and
 * it means one thing: the console is connected to something. Underneath it,
 * the hunt strip names the state of the current investigation in one line, so
 * a reader across the room knows whether the number is still moving.
 *
 * Monitor and ring take the whole area below the masthead rather than a
 * column, because both are about the pack rather than about a case, and
 * putting them beside a case would invite reading them as one.
 */

import { useCallback, useEffect, useState } from "react";

import { ActionRail } from "@/components/console/ActionRail";
import { ApprovalsDrawer } from "@/components/console/ApprovalsDrawer";
import { CasePanel, type CasePanelState } from "@/components/console/CasePanel";
import { GraphCanvasView, canvasSummary } from "@/components/console/GraphCanvasView";
import { Icon, IconSprite } from "@/components/console/icons";
import { MonitorView } from "@/components/console/MonitorView";
import { ConsoleChip, Overline } from "@/components/console/parts";
import { QueueRail } from "@/components/console/QueueRail";
import { RingView } from "@/components/console/RingView";
import { useDemo, type Act } from "@/components/console/useDemo";
import { huntState, pct, toneTint, toneVar, verdictTone } from "@/components/console/vm";
import { RoleSwitcher } from "@/components/shell/RoleSwitcher";
import { useApprovals, useCanvas, useCases, useMeta, useRunAll } from "@/lib/hooks";
import { cn } from "@/lib/cn";

export type ViewId = "console" | "monitor" | "ring";

const VIEWS: { id: ViewId; label: string; icon: "window" | "baseline" | "ring" }[] = [
  { id: "console", label: "Console", icon: "window" },
  { id: "monitor", label: "Monitor", icon: "baseline" },
  { id: "ring", label: "Ring", icon: "ring" },
];

export function Console({
  initialCase = "",
  initialView = "console",
}: {
  initialCase?: string;
  initialView?: ViewId;
}) {
  const [view, setView] = useState<ViewId>(initialView);
  const [selected, setSelected] = useState(initialCase);
  const [drawer, setDrawer] = useState(false);
  const [pane, setPane] = useState(true);
  const [liveRunId, setLiveRunId] = useState<string | null>(null);
  const [state, setState] = useState<CasePanelState>({
    running: false,
    probability: null,
    note: "",
    progress: 0,
  });

  const runAll = useRunAll();
  const [armed, setArmed] = useState(false);
  const cases = useCases({ limit: 100 });
  const approvals = useApprovals("pending");
  const meta = useMeta();
  const rows = cases.data?.items ?? [];

  // The first case the queue offers, so the console is never empty on load.
  useEffect(() => {
    if (!selected && rows.length > 0) {
      const first = rows[0];
      if (first) setSelected(first.case_id);
    }
  }, [rows, selected]);

  // The graph pane costs a live TigerGraph round trip, so it is fetched only
  // when it is actually on screen.
  const canvas = useCanvas(selected, pane && view === "console");

  const row = rows.find((item) => item.case_id === selected) ?? null;
  const verdict = row?.verdict ?? null;
  const hunt = huntState(
    state.running,
    state.probability ?? row?.fraud_probability ?? 0.5,
    verdict,
    state.note,
  );
  const pendingApprovals = approvals.data?.items.length ?? 0;

  const applyAct = useCallback((act: Act) => {
    setSelected(act.caseId);
    setLiveRunId(null);
    setView(act.view);
  }, []);
  const demo = useDemo(rows, applyAct);

  const tally = (["fraud", "uncertain", "legitimate"] as const).map((key) => ({
    key,
    n: rows.filter((item) => item.verdict === key).length,
  }));

  return (
    <div className="flex h-dvh min-w-[800px] flex-col overflow-hidden bg-bg">
      <IconSprite />

      <header className="flex h-[54px] shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
        <div className="flex shrink-0 items-center gap-2.5">
          {/* eslint-disable-next-line @next/next/no-img-element -- a 26px mark; next/image adds nothing here */}
          <img src="/logo.png" alt="" width={26} height={26} className="rounded-md" />
          <div className="flex flex-col leading-none">
            <span className="text-[14px] font-bold tracking-[0.14em]">SENTINEL</span>
            <span className="mt-0.5 font-mono text-[9px] leading-snug tracking-[0.06em] text-fg-muted">
              agentic fraud investigator
            </span>
          </div>
        </div>

        <div className="hidden shrink-0 items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 xl:flex">
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              meta.data ? "animate-s-pulse bg-success" : "bg-border-strong",
            )}
          />
          <span className="whitespace-nowrap font-mono text-[10px] font-medium text-fg-muted">
            {meta.data ? `TigerGraph · API v${meta.data.version}` : "connecting…"}
          </span>
        </div>

        <div
          role="tablist"
          aria-label="View"
          className="flex shrink-0 gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5"
        >
          {VIEWS.map((item) => {
            const on = view === item.id;
            return (
              <button
                key={item.id}
                role="tab"
                aria-selected={on}
                type="button"
                onClick={() => setView(item.id)}
                className={cn(
                  "flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5",
                  "text-[10.5px] font-semibold",
                  on ? "bg-surface text-accent shadow-1" : "text-fg-muted hover:text-fg",
                )}
              >
                <Icon name={item.icon} size={12} />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="flex-1" />

        <div className="hidden items-center gap-2.5 rounded-md border border-border bg-surface-2 px-2.5 py-1.5 lg:flex">
          {tally.map((item) => (
            <span
              key={item.key}
              className="flex items-center gap-1.5 font-mono text-[10px] font-semibold text-fg-muted"
            >
              <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
                <rect width="8" height="8" rx="2" fill={toneVar(verdictTone(item.key))} />
              </svg>
              {item.n} {item.key}
            </span>
          ))}
        </div>

        <button
          type="button"
          onClick={() => (demo.running ? demo.stop() : demo.start())}
          className={cn(
            "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border px-3 py-2 text-[11px] font-semibold",
            demo.running
              ? "border-accent bg-accent text-accent-contrast"
              : "border-border-strong bg-surface-2 hover:bg-accent-tint",
          )}
        >
          <Icon name="bolt" size={13} />
          {demo.running ? "Stop demo" : "Run demo"}
        </button>

        <button
          type="button"
          onClick={() => {
            if (!armed) {
              setArmed(true);
              return;
            }
            setArmed(false);
            runAll.mutate();
          }}
          onBlur={() => setArmed(false)}
          disabled={runAll.isPending}
          title="Starts a real batch: twenty live investigations against the graph and the model."
          className={cn(
            "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border px-3 py-2 text-[11px] font-semibold",
            armed
              ? "border-danger bg-danger-tint text-danger"
              : "border-border-strong bg-surface-2 hover:bg-accent-tint",
          )}
        >
          <Icon name="bolt" size={13} style={{ color: armed ? undefined : "var(--accent)" }} />
          {runAll.isPending ? "starting…" : armed ? "Confirm — 20 live runs" : "Run all 20"}
        </button>

        <RoleSwitcher />

        <button
          type="button"
          onClick={() => setDrawer(true)}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-border-strong bg-surface-2 px-3 py-2 text-[11px] font-semibold hover:bg-accent-tint"
        >
          <Icon name="check" size={13} style={{ color: "var(--authority)" }} />
          Approvals
          <span
            className="inline-flex h-[17px] min-w-[17px] items-center justify-center rounded-full px-1 font-mono text-[10px] font-bold text-surface"
            style={{
              background: pendingApprovals > 0 ? toneVar("warning") : "var(--border-strong)",
            }}
          >
            {pendingApprovals}
          </span>
        </button>
      </header>

      <div className="bg-runway h-[3px] shrink-0 opacity-85" aria-hidden="true" />

      <div className="flex h-[26px] shrink-0 items-center gap-2.5 overflow-hidden border-b border-border bg-surface px-4">
        <span
          className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm px-[7px] py-[3px] font-mono text-[9px] font-bold uppercase tracking-[0.06em]"
          style={{ background: toneTint(hunt.tone), color: toneVar(hunt.tone) }}
        >
          <Icon name={hunt.glyph} size={11} />
          {hunt.label}
        </span>
        <span className="truncate font-mono text-[9.5px] font-medium tracking-[0.06em] text-fg-subtle">
          {hunt.note}
        </span>
        <span className="relative mx-1 h-0.5 flex-1 overflow-hidden rounded-full bg-surface-3">
          <span
            className="absolute inset-y-0 left-0 rounded-full"
            style={{
              width: pct(state.progress),
              background: toneVar(hunt.tone),
              transition: "width var(--motion-slow) var(--ease-standard)",
            }}
          />
        </span>
        <span className="font-mono text-[9.5px] font-semibold tabular-nums text-fg-muted">
          {pct(state.progress)}
        </span>
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          className="grid h-full min-h-0"
          style={{
            gridTemplateColumns: pane ? "252px minmax(0,1fr) 404px" : "252px minmax(0,1fr)",
            // One row, pinned to the container's height. Left to itself the
            // implicit row grows to the tallest pane, which pushes the panes
            // past the viewport and leaves their own overflow rules nothing
            // to bite on.
            gridTemplateRows: "minmax(0,1fr)",
          }}
        >
          <QueueRail
            rows={rows}
            selected={selected}
            runningCase={state.running ? selected : null}
            onSelect={(id) => {
              setSelected(id);
              setLiveRunId(null);
              setView("console");
            }}
          />

          {selected ? (
            <CasePanel
              key={selected}
              caseId={selected}
              liveRunId={liveRunId}
              onState={setState}
              onStart={setLiveRunId}
            />
          ) : (
            <main className="flex items-center justify-center text-caption text-fg-subtle">
              {cases.isLoading ? "Loading the queue…" : "Pick a case."}
            </main>
          )}

          {pane ? (
            <aside className="flex min-h-0 flex-col border-l border-border bg-surface">
              <div className="flex shrink-0 items-center justify-between px-3.5 pb-1.5 pt-3">
                <Overline className="tracking-[0.14em]">Graph evidence</Overline>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[9px] text-fg-subtle">
                    {canvas.isLoading ? "loading…" : canvasSummary(canvas.data ?? null)}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPane(false)}
                    className="rounded-md border border-border-strong px-2 py-1 font-mono text-[9px] font-semibold text-fg-muted hover:text-fg"
                  >
                    hide ›
                  </button>
                </div>
              </div>
              {canvas.error ? (
                <p className="mx-3 rounded-lg border border-dashed border-border px-3 py-6 text-center text-caption text-fg-subtle">
                  The graph did not answer: {(canvas.error as Error).message}
                </p>
              ) : (
                <GraphCanvasView canvas={canvas.data ?? null} live={state.running} />
              )}
              {selected ? (
                <ActionRail caseId={selected} onOpenApprovals={() => setDrawer(true)} />
              ) : null}
            </aside>
          ) : (
            <button
              type="button"
              onClick={() => setPane(true)}
              className="fixed bottom-4 right-4 z-sticky rounded-md border border-border-strong bg-surface px-3 py-2 font-mono text-[10px] font-semibold shadow-2"
            >
              ‹ graph
            </button>
          )}
        </div>

        {view === "monitor" ? <MonitorView /> : null}
        {view === "ring" ? <RingView /> : null}
      </div>

      {demo.act ? (
        <div className="animate-s-in fixed bottom-6 left-1/2 z-drawer flex max-w-[min(92vw,900px)] -translate-x-1/2 items-center gap-3.5 rounded-xl bg-fg px-4 py-3 shadow-3">
          <ConsoleChip tone="accent" fill="solid">
            {demo.act.title}
          </ConsoleChip>
          <span className="text-[12.5px] leading-snug text-bg">{demo.act.caption}</span>
          <button
            type="button"
            onClick={demo.stop}
            className="ml-1.5 shrink-0 rounded-md border border-fg-muted px-2.5 py-1.5 font-mono text-[10px] font-semibold text-bg"
          >
            stop
          </button>
        </div>
      ) : null}

      {runAll.isError ? (
        <div className="animate-s-in fixed bottom-6 left-1/2 z-drawer -translate-x-1/2 rounded-xl border border-danger bg-danger-tint px-4 py-3 text-[12px] text-danger">
          Could not start the batch: {(runAll.error as Error).message}
        </div>
      ) : null}

      <ApprovalsDrawer open={drawer} onClose={() => setDrawer(false)} />
    </div>
  );
}
