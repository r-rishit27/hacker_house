"use client";

/**
 * Replaying a recorded run at the speed it was investigated.
 *
 * Nineteen of the twenty cases were investigated days ago by the batch runner.
 * Their evidence is on disk in full — every posting, every probability, in
 * order — but dropping eighteen rows onto the screen at once loses the thing
 * that matters most about this system: **the number moves, and the order it
 * moves in is the argument.** A reader who sees the finished list sees a
 * conclusion. A reader who watches the recurring-charge probe drag 0.81 down
 * to 0.07 sees why.
 *
 * So a finished case replays. This is not a simulation and it invents nothing:
 * every frame is a row that was really posted, revealed in the order it was
 * really posted in. The console labels it `replay` and says which run it came
 * from, because a replay that looks like a live run is a lie about when the
 * work happened.
 *
 * The cadence comes from `--stream-step` so the CSS that decorates a streaming
 * row and the hook that reveals it cannot drift apart. Under
 * `prefers-reduced-motion` there is no reveal at all: everything is shown at
 * once, which is the same information without the theatre.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const FALLBACK_STEP_MS = 460;

/** `--stream-step` in milliseconds, read once per mount. */
function stepMs(): number {
  if (typeof window === "undefined") return FALLBACK_STEP_MS;
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--stream-step").trim();
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : FALLBACK_STEP_MS;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export interface Replay<T> {
  /** The prefix revealed so far. Never empty once `total` is non-zero. */
  shown: T[];
  /** How many have been revealed, including the ones already on screen. */
  revealed: number;
  total: number;
  /** True while frames are still arriving. */
  running: boolean;
  /** Reveal everything now — the skip button, and what a second click does. */
  finish: () => void;
  /** Start again from the first frame. */
  restart: () => void;
}

/**
 * Reveal `items` one at a time.
 *
 * `enabled` false means show everything immediately: that is the right
 * behaviour while a case is genuinely streaming (the stream is already
 * paced), and while the reader has asked for reduced motion.
 */
export function useReplay<T>(items: readonly T[], enabled: boolean): Replay<T> {
  const [revealed, setRevealed] = useState(items.length);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const total = items.length;

  const stop = useCallback(() => {
    if (timer.current !== null) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const begin = useCallback(() => {
    stop();
    if (!enabled || total === 0 || prefersReducedMotion()) {
      setRevealed(total);
      return;
    }
    setRevealed(1);
    const every = stepMs();
    timer.current = setInterval(() => {
      setRevealed((current) => {
        if (current >= total) {
          stop();
          return total;
        }
        return current + 1;
      });
    }, every);
  }, [enabled, total, stop]);

  // Restart whenever the reel itself changes — a different case, or the same
  // case re-run. `total` standing in for the array keeps this from firing on
  // every render, because the query layer hands back a new array each time.
  useEffect(() => {
    begin();
    return stop;
  }, [begin, stop]);

  const shown = useMemo(() => items.slice(0, Math.max(revealed, 0)), [items, revealed]);

  return {
    shown,
    revealed: Math.min(revealed, total),
    total,
    running: revealed < total,
    finish: useCallback(() => {
      stop();
      setRevealed(total);
    }, [stop, total]),
    restart: begin,
  };
}
