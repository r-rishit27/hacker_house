"use client";

/**
 * What to do now — and who is allowed to do it.
 *
 * The toggle at the top is the point of the panel. `initial` is what the
 * agent recommended before it asked anyone anything; `final` is after the
 * answer came back. Flipping between them on a case that changed is the
 * clearest thirty seconds in the whole console, which is why it is a switch
 * and not two stacked lists.
 *
 * Pressing an `L1` or `L2` action returns 403. That is the design, not a
 * failure: the server enqueues the approval, names the roles that can grant
 * it, and this panel renders the denial as the next step with a link into the
 * inbox. The client never decides what it may do — it only says who it is.
 */

import { useState } from "react";

import { Icon } from "@/components/console/icons";
import { ConsoleChip, Overline } from "@/components/console/parts";
import { actionTone, humanise, money, routeTone, toneVar } from "@/components/console/vm";
import { ApiProblem } from "@/lib/api";
import { useActions, useExecute } from "@/lib/hooks";
import { ROLE_LABELS } from "@/lib/role";
import type { ActionRow, Role } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

type Phase = "initial" | "final";

export function ActionRail({
  caseId,
  onOpenApprovals,
}: {
  caseId: string;
  onOpenApprovals: () => void;
}) {
  const { data, isLoading, error } = useActions(caseId);
  const execute = useExecute(caseId);
  const [phase, setPhase] = useState<Phase>("final");
  const [pressed, setPressed] = useState("");

  if (error) {
    return (
      <p className="px-3 py-6 text-center text-caption text-fg-subtle">
        No action plan for this case yet.
      </p>
    );
  }
  if (isLoading || !data) {
    return <p className="px-3 py-6 text-center text-caption text-fg-subtle">Loading actions…</p>;
  }

  const rows = phase === "initial" ? data.initial : data.final;
  const denial = execute.error instanceof ApiProblem ? execute.error : null;

  const run = (row: ActionRow) => {
    setPressed(row.action);
    execute.reset();
    execute.mutate({
      case_id: caseId,
      action: row.action,
      phase: "final",
      payload: {},
      on_denied: "enqueue",
    });
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-5 pt-3.5">
      <div className="mb-2.5 flex items-center justify-between">
        <Overline className="tracking-[0.14em]">Next best action</Overline>
        <div
          role="group"
          aria-label="Recommendation phase"
          className="flex gap-0.5 rounded-md border border-border bg-surface-2 p-0.5"
        >
          {(["initial", "final"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setPhase(value)}
              aria-pressed={phase === value}
              className={cn(
                "rounded-sm px-2.5 py-1 font-mono text-[10px] font-semibold",
                phase === value ? "bg-accent-tint text-fg" : "text-fg-muted hover:text-fg",
              )}
            >
              {value}
            </button>
          ))}
        </div>
      </div>

      {/* The runner writes the literal string "nothing" when the two phases
          agree. Rendering it as a finding is worse than rendering nothing. */}
      {phase === "final" && data.what_changed && data.what_changed !== "nothing" ? (
        <div className="mb-2.5 flex items-start gap-2.5 rounded-lg border border-warning-tint bg-warning-tint px-3 py-2.5">
          <Icon name="ask" size={14} className="mt-px shrink-0" style={{ color: "var(--warning)" }} />
          <p className="text-[11.5px] leading-snug" style={{ color: "var(--warning)" }}>
            {data.what_changed}
          </p>
        </div>
      ) : null}

      <ul className="flex flex-col gap-2">
        {rows.map((row, index) => {
          const tone = routeTone(row.route);
          const mine = pressed === row.action;
          const problem = mine ? denial : null;
          const done = row.outcome !== null || (mine && execute.isSuccess);
          return (
            <li
              key={`${row.action}-${index}`}
              className="rounded-lg border bg-surface px-3 py-3"
              style={{ borderColor: done ? toneVar(tone) : "var(--border)" }}
            >
              <div className="flex items-center gap-2">
                <span className="inline-flex h-[18px] w-[18px] items-center justify-center rounded-sm bg-surface-3 font-mono text-[9px] font-bold text-fg-muted">
                  {index + 1}
                </span>
                <span
                  className="font-mono text-[11.5px] font-bold"
                  style={{ color: toneVar(actionTone(row.action)) }}
                >
                  {row.action}
                </span>
                <span className="flex-1" />
                <ConsoleChip tone={tone}>{row.route}</ConsoleChip>
              </div>

              {row.route_changed && row.answer_route ? (
                <p className="mt-1.5 font-mono text-[9.5px] text-fg-subtle">
                  answer file said {row.answer_route}; recomputed at this exposure
                </p>
              ) : null}

              <p className="mt-2 text-[11.5px] leading-snug text-fg-muted">{row.reason}</p>

              <button
                type="button"
                disabled={done || (mine && execute.isPending)}
                onClick={() => run(row)}
                className={cn(
                  "mt-2.5 w-full rounded-md border px-2 py-2 font-mono text-[10.5px] font-semibold tracking-[0.04em]",
                  "transition-colors duration-fast disabled:cursor-default",
                  done ? "" : "hover:bg-surface-2",
                )}
                style={{
                  borderColor: done ? toneVar(tone) : "var(--border-strong)",
                  background: done ? `color-mix(in srgb, ${toneVar(tone)} 10%, transparent)` : "var(--surface-2)",
                  color: done ? toneVar(tone) : "var(--fg)",
                }}
              >
                {done
                  ? `✓ ${row.outcome ?? "executed"}`
                  : mine && execute.isPending
                    ? "working…"
                    : row.route === "auto"
                      ? "Execute now"
                      : `Send to ${row.route} approval`}
              </button>

              {problem ? (
                <div className="mt-2 rounded-md border border-warning-tint bg-warning-tint px-2.5 py-2">
                  <p className="text-[11px] leading-snug" style={{ color: "var(--warning)" }}>
                    {problem.detail}
                  </p>
                  {problem.approvers.length > 0 ? (
                    <p className="mt-1 font-mono text-[9.5px]" style={{ color: "var(--warning)" }}>
                      {problem.approvers.map((r) => ROLE_LABELS[r as Role]).join(" or ")} can approve
                    </p>
                  ) : null}
                  {problem.approval ? (
                    <button
                      type="button"
                      onClick={onOpenApprovals}
                      className="mt-1.5 font-mono text-[10px] font-semibold underline"
                      style={{ color: "var(--warning)" }}
                    >
                      Open the approvals inbox →
                    </button>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      {rows.length === 0 ? (
        <p className="px-2 py-6 text-center text-caption text-fg-subtle">
          No {phase} recommendation on this case.
        </p>
      ) : null}

      <p className="mt-3 font-mono text-[9px] leading-relaxed text-fg-subtle">
        exposure {money(data.exposure_usd)} · acting as {ROLE_LABELS[data.role as Role]} ·
        every action but {humanise("CREATE_CASE")} is simulated
      </p>
    </div>
  );
}
