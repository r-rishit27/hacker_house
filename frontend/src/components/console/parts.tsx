/**
 * The small shapes the console repeats.
 *
 * Each one exists because it appears in at least three places and got it
 * wrong in one of them the first time: the dial in the queue and the case
 * header, the overline above every panel, the tile in the monitor and the
 * ring summary.
 */

import type { ReactNode } from "react";

import { Icon, type IconName } from "@/components/console/icons";
import { meterVar, toneTint, toneVar, type Tone } from "@/components/console/vm";
import { cn } from "@/lib/cn";

/** A small uppercase mono label. The console's only section heading. */
export function Overline({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "font-mono text-[10px] font-semibold uppercase leading-none tracking-[0.12em] text-fg-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * A tinted chip. Takes a tone rather than a colour, and never a hex.
 *
 * `solid` is for a chip that must be legible against a dark caption bar;
 * everything else is a tint, so a row of six chips does not fight the text.
 */
export function ConsoleChip({
  tone = "neutral",
  fill = "tint",
  icon,
  children,
  className,
}: {
  tone?: Tone;
  fill?: "tint" | "solid";
  icon?: IconName;
  children: ReactNode;
  className?: string;
}) {
  const solid = fill === "solid";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-sm px-[7px] py-[3px]",
        "font-mono text-[9px] font-bold uppercase leading-[1.4] tracking-[0.06em]",
        className,
      )}
      style={{
        background: solid ? toneVar(tone) : toneTint(tone),
        color: solid ? "var(--surface)" : toneVar(tone),
      }}
    >
      {icon ? <Icon name={icon} size={10} /> : null}
      {children}
    </span>
  );
}

/**
 * The probability dial.
 *
 * A ring rather than a bar because the number is bounded at both ends and a
 * reader should be able to tell 0.9 from 0.6 at rail width without reading
 * the digits. The colour is the meter band, so the dial says "act", "decide"
 * or "clear" before the number is read at all.
 */
export function Dial({
  p,
  size = 62,
  stroke = 6,
  label,
  muted = false,
}: {
  p: number | null;
  size?: number;
  stroke?: number;
  label?: string;
  muted?: boolean;
}) {
  const radius = (size - stroke) / 2 - 1;
  const circumference = 2 * Math.PI * radius;
  const value = p ?? 0;
  const colour = muted || p === null ? "var(--border-strong)" : meterVar(value);
  const centre = size / 2;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle
          cx={centre}
          cy={centre}
          r={radius}
          fill="none"
          stroke="var(--meter-track)"
          strokeWidth={stroke}
        />
        <circle
          cx={centre}
          cy={centre}
          r={radius}
          fill="none"
          stroke={colour}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - value)}
          transform={`rotate(-90 ${centre} ${centre})`}
          style={{ transition: "stroke-dashoffset var(--motion-slow) var(--ease-standard)" }}
        />
      </svg>
      {label ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="font-mono font-bold tabular-nums leading-none"
            style={{ color: colour, fontSize: size * 0.24 }}
          >
            {p === null ? "—" : p.toFixed(2)}
          </span>
          <span className="mt-0.5 font-mono text-[7px] leading-none text-fg-muted">{label}</span>
        </div>
      ) : null}
    </div>
  );
}

/** A labelled number in a bordered box. The monitor and ring summaries. */
export function StatTile({
  label,
  value,
  note,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: Tone;
  icon?: IconName;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3.5 py-3">
      <div className="flex items-center gap-1.5">
        {icon ? <Icon name={icon} size={12} style={{ color: toneVar(tone) }} /> : null}
        <span className="font-mono text-[9px] font-medium uppercase leading-none tracking-[0.09em] text-fg-muted">
          {label}
        </span>
      </div>
      <div
        className="mt-2 font-mono text-[21px] font-bold leading-none tabular-nums"
        style={{ color: tone === "neutral" ? "var(--fg)" : toneVar(tone) }}
      >
        {value}
      </div>
      {note ? <div className="mt-1.5 text-[10px] leading-snug text-fg-subtle">{note}</div> : null}
    </div>
  );
}

/** A bordered panel with an overline header. */
export function Panel({
  title,
  aside,
  children,
  bodyClassName,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  bodyClassName?: string;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <header className="flex items-center justify-between gap-3 border-b border-border px-3.5 py-2.5">
        <Overline>{title}</Overline>
        {aside ? (
          <span className="font-mono text-[9px] font-medium text-fg-subtle">{aside}</span>
        ) : null}
      </header>
      <div className={bodyClassName ?? "p-3.5"}>{children}</div>
    </section>
  );
}
