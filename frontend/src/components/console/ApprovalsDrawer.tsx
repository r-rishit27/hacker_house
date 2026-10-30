"use client";

/**
 * Everything waiting on a human, in a drawer rather than a page.
 *
 * A drawer because approving is something you do *while* looking at a case,
 * not instead of looking at one — navigating away to approve and back to see
 * the effect is how the permission boundary stops being legible.
 *
 * The important behaviour is the refusal. An analyst who tries to approve
 * their own L2 action gets `role_insufficient_for_approval` back, and that
 * refusal is rendered in place. The button is not hidden from them: a
 * boundary you can only discover by not seeing a control is a boundary nobody
 * can demonstrate.
 */

import { useState } from "react";

import { Icon } from "@/components/console/icons";
import { ConsoleChip } from "@/components/console/parts";
import { money, routeTone, toneVar } from "@/components/console/vm";
import { ApiProblem } from "@/lib/api";
import { useApprovals, useDecideApproval } from "@/lib/hooks";
import { ROLE_LABELS, useRole } from "@/lib/role";
import type { Role } from "@/lib/generated/contract";

export function ApprovalsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data } = useApprovals();
  const decide = useDecideApproval();
  const { role } = useRole();
  const [acting, setActing] = useState("");

  if (!open) return null;

  const items = data?.items ?? [];
  const problem = decide.error instanceof ApiProblem ? decide.error : null;

  return (
    <div
      className="fixed inset-0 z-drawer flex justify-end bg-[rgb(60_42_26_/_0.34)]"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="animate-s-in flex h-full w-[430px] max-w-full flex-col border-l border-border-strong bg-surface"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-label="Approval inbox"
      >
        <header className="flex shrink-0 items-center gap-2.5 border-b border-border px-4 py-4">
          <Icon name="check" size={17} style={{ color: "var(--authority)" }} />
          <span className="text-[13px] font-bold tracking-[0.04em]">APPROVAL INBOX</span>
          <span className="flex-1" />
          <span className="font-mono text-[9.5px] text-fg-subtle">as {ROLE_LABELS[role]}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="h-[26px] w-[26px] rounded-md border border-border-strong bg-surface-2 text-[13px] font-semibold text-fg-muted hover:text-fg"
          >
            ×
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-3.5">
          {items.map((item) => {
            const tone = routeTone(item.route);
            const mine = acting === item.approval_id;
            const settled = item.status !== "pending";
            return (
              <div
                key={item.approval_id}
                className="rounded-lg border bg-surface px-3.5 py-3"
                style={{ borderColor: settled ? toneVar(tone) : "var(--border)" }}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10.5px] font-semibold text-fg-muted">
                    {item.case_id}
                  </span>
                  <span className="flex-1" />
                  <ConsoleChip tone={tone}>{item.route}</ConsoleChip>
                  <ConsoleChip tone={settled ? "success" : "warning"}>{item.status}</ConsoleChip>
                </div>
                <div className="mt-2 font-mono text-[12px] font-bold">{item.action}</div>
                <p className="mt-1.5 text-[11.5px] leading-snug text-fg-muted">
                  Requested by {ROLE_LABELS[item.requested_role]} on the {item.phase}{" "}
                  recommendation. {item.approvers.map((r) => ROLE_LABELS[r]).join(" or ")} can
                  grant it.
                </p>
                <div className="mt-2.5 flex items-center gap-2">
                  <span className="font-mono text-[10px] tabular-nums text-fg-muted">
                    exposure {money(item.exposure_usd)}
                  </span>
                  <span className="flex-1" />
                  {!settled ? (
                    <>
                      <button
                        type="button"
                        disabled={mine && decide.isPending}
                        onClick={() => {
                          setActing(item.approval_id);
                          decide.reset();
                          decide.mutate({ id: item.approval_id, approve: false, note: "" });
                        }}
                        className="rounded-md border border-border-strong bg-surface-2 px-3 py-1.5 font-mono text-[10.5px] font-semibold text-fg-muted hover:text-fg"
                      >
                        Reject
                      </button>
                      <button
                        type="button"
                        disabled={mine && decide.isPending}
                        onClick={() => {
                          setActing(item.approval_id);
                          decide.reset();
                          decide.mutate({ id: item.approval_id, approve: true, note: "" });
                        }}
                        className="rounded-md border px-3 py-1.5 font-mono text-[10.5px] font-semibold"
                        style={{ borderColor: toneVar(tone), color: toneVar(tone) }}
                      >
                        Approve as {item.route === "L2" ? "fraud manager" : "team lead"}
                      </button>
                    </>
                  ) : (
                    <span className="font-mono text-[10.5px]" style={{ color: toneVar(tone) }}>
                      ✓ {item.status}
                      {item.decided_role ? ` by ${ROLE_LABELS[item.decided_role]}` : ""}
                    </span>
                  )}
                </div>
                {mine && problem ? (
                  <p className="mt-2 rounded-md bg-danger-tint px-2.5 py-2 text-[11px] leading-snug text-danger">
                    {problem.detail}
                  </p>
                ) : null}
              </div>
            );
          })}

          {items.length === 0 ? (
            <p className="px-3 py-9 text-center font-mono text-[11px] leading-relaxed text-fg-subtle">
              Nothing waiting on a human.
              <br />
              Send an L1 or L2 action for approval to see it here.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
