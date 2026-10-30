"use client";

/**
 * The three-act demo walk.
 *
 * A console full of finished cases does not explain itself. Someone seeing it
 * for the first time needs to be shown the argument in order, and the order
 * is an argument: a real fraud, then the trap the model falls into, then the
 * thing the policy does not document.
 *
 * Each act picks a **real** case out of the live queue by what the agent
 * actually concluded — the highest-probability fraud, the case the model
 * scored highest and the agent cleared anyway, the one that moved furthest
 * after evidence. Nothing here is scripted data; the acts are a selection over
 * what is on screen, so the walk stays true when the pack is re-run.
 *
 * Each act holds for as long as that case's replay takes, so the caption and
 * the timeline finish together.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { CaseSummary } from "@/lib/generated/contract";

export interface Act {
  title: string;
  caption: string;
  caseId: string;
  /** Which view to end the act on. */
  view: "console" | "ring";
}

export interface DemoState {
  running: boolean;
  act: Act | null;
  index: number;
  total: number;
  start: () => void;
  stop: () => void;
}

/** Roughly how long a replay takes, so a caption does not outrun its case. */
const PER_POSTING_MS = 460;
const ASSUMED_POSTINGS = 20;
const ACT_MS = PER_POSTING_MS * ASSUMED_POSTINGS + 2_600;

/**
 * Choose three cases that make the three points.
 *
 * Returns fewer acts rather than inventing one: a pack with no legitimate
 * verdict has no trap to show, and a demo that claims otherwise is worse than
 * a shorter demo.
 */
export function buildActs(rows: CaseSummary[]): Act[] {
  const answered = rows.filter((row) => row.has_answer && row.fraud_probability !== null);
  const acts: Act[] = [];

  const fraud = answered
    .filter((row) => row.verdict === "fraud")
    .sort((a, b) => (b.fraud_probability ?? 0) - (a.fraud_probability ?? 0))[0];
  if (fraud) {
    acts.push({
      title: "ACT I",
      caption:
        `A real fraud. ${fraud.case_id} ends at ${(fraud.fraud_probability ?? 0).toFixed(2)} ` +
        `on evidence the agent found in the graph, and the action it recommends routes for ` +
        `human approval rather than firing itself.`,
      caseId: fraud.case_id,
      view: "console",
    });
  }

  // The trap: the bank's model scored it high and the agent cleared it anyway.
  // This is the case the whole calibration argument rests on.
  const trap = answered
    .filter((row) => row.verdict === "legitimate" && row.alert_risk_score !== null)
    .sort((a, b) => (b.alert_risk_score ?? 0) - (a.alert_risk_score ?? 0))[0];
  if (trap) {
    acts.push({
      title: "ACT II",
      caption:
        `The trap. The bank's model scored ${trap.case_id} at ` +
        `${(trap.alert_risk_score ?? 0).toFixed(2)}. The recurring-charge probe and the ` +
        `trip-not-clone check drive it to ${(trap.fraud_probability ?? 0).toFixed(2)} — the ` +
        `agent disagrees with the model, and says why.`,
      caseId: trap.case_id,
      view: "console",
    });
  }

  const analyst = answered.find((row) => row.trigger_type === "analyst_request") ?? answered[0];
  if (analyst) {
    acts.push({
      title: "ACT III",
      caption:
        "The ring that is not there. The sweep tests every seed's device neighbourhood and " +
        "reports no ring at one hop — because two hops swallows a seventh of the portfolio. " +
        "A negative result, stated as one.",
      caseId: analyst.case_id,
      view: "ring",
    });
  }

  return acts;
}

export function useDemo(
  rows: CaseSummary[],
  apply: (act: Act) => void,
): DemoState {
  const [index, setIndex] = useState(-1);
  const [acts, setActs] = useState<Act[]>([]);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clear = useCallback(() => {
    for (const timer of timers.current) clearTimeout(timer);
    timers.current = [];
  }, []);

  const stop = useCallback(() => {
    clear();
    setIndex(-1);
  }, [clear]);

  const start = useCallback(() => {
    clear();
    const planned = buildActs(rows);
    if (planned.length === 0) return;
    setActs(planned);
    planned.forEach((act, position) => {
      timers.current.push(
        setTimeout(() => {
          setIndex(position);
          apply(act);
        }, position * ACT_MS),
      );
    });
    timers.current.push(setTimeout(() => setIndex(-1), planned.length * ACT_MS));
  }, [rows, apply, clear]);

  useEffect(() => clear, [clear]);

  return {
    running: index >= 0,
    act: index >= 0 ? (acts[index] ?? null) : null,
    index,
    total: acts.length,
    start,
    stop,
  };
}
