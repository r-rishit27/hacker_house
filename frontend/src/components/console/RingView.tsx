"use client";

/**
 * Ring discovery: the finding, and the one thing worth drawing.
 *
 * Two facts have to live on this screen at once, and earlier versions got the
 * balance wrong in both directions.
 *
 * **The finding is negative.** At one hop — the hop R6 is defined on — no two
 * of the twenty seeds share a device profile. There is no ring on this pack,
 * the headline says so, and nothing here is allowed to imply otherwise.
 *
 * **There is still something to look at.** Widening to two hops with the same
 * specificity gate, one fingerprint carries twelve cards belonging to twelve
 * different customers, seven of which the bank has already closed as
 * confirmed fraud. That satisfies the same component test the ring gate
 * applies. It is not a ring, because it is not at one hop, and the panel says
 * so in as many words — but drawing 118 nodes of undifferentiated two-hop
 * mesh instead, as this screen used to, buried a real finding in a texture.
 *
 * So the canvas draws that component the way the design study draws a ring:
 * the shared profile in the middle, its cards around it, each one named, the
 * ones the bank has already confirmed marked as such. Every number beside it
 * comes from `exploration/rings.json`, which `python -m sentinel explore
 * rings` wrote — the page and the terminal cannot disagree.
 */

import { useMemo } from "react";

import { Icon } from "@/components/console/icons";
import { ConsoleChip, Overline, StatTile } from "@/components/console/parts";
import { money, toneVar } from "@/components/console/vm";
import { useRings } from "@/lib/hooks";
import type { RingComponent } from "@/lib/generated/contract";

const W = 900;
const H = 620;
const CX = W / 2;
const CY = H / 2;

/** The card ring around the shared profile. */
const RX = 300;
const RY = 198;

interface Dot {
  x: number;
  y: number;
  r: number;
  label: string;
  sub: string;
  tone: "danger" | "accent";
  hub?: boolean;
}

interface Layout {
  dots: Dot[];
  /** Indices of the cards. Every edge runs from the hub, which is index 0. */
  edges: number[];
}

/** `C08623-K2` -> `C08623`. The dataset's own convention. */
function customerOf(card: string): string {
  return card.split("-", 1)[0] ?? card;
}

/**
 * One shared profile at the centre, its cards around it.
 *
 * A star, not a force layout, because a star is what this is: every card here
 * is joined to every other *through* the one fingerprint, and a physics
 * simulation would obscure that by implying the cards have relationships of
 * their own. Deterministic, so two people looking at it on a call see the
 * same picture.
 */
function layoutFocus(component: RingComponent): Layout {
  const confirmed = new Set(component.confirmed_fraud_cards);
  const seeds = new Set(component.seeds);
  const device = component.devices[0] ?? "shared profile";

  const dots: Dot[] = [
    {
      x: CX,
      y: CY,
      r: 46,
      label: device,
      sub: `${component.cards.length} cards · ${component.customers.length} customers`,
      tone: "danger",
      hub: true,
    },
  ];
  const edges: number[] = [];

  component.cards.forEach((card, index, all) => {
    const angle = -Math.PI / 2 + (index / all.length) * Math.PI * 2;
    dots.push({
      x: CX + Math.cos(angle) * RX,
      y: CY + Math.sin(angle) * RY,
      r: seeds.has(card) ? 25 : 21,
      label: card,
      sub: seeds.has(card)
        ? "benchmark seed"
        : confirmed.has(card)
          ? "confirmed fraud"
          : customerOf(card),
      // Danger is kept for what the bank has already called fraud and for the
      // seed this pack actually asks about. Everything else is a card and
      // nothing more: the drawing must not convict by hue.
      tone: seeds.has(card) || confirmed.has(card) ? "danger" : "accent",
    });
    edges.push(dots.length - 1);
  });

  return { dots, edges };
}

export function RingView() {
  const { data, isLoading, error } = useRings();

  // A real one-hop ring wins if the sweep ever finds one; the two-hop
  // candidate is only what gets drawn in its absence.
  const component = data?.components[0] ?? data?.focus ?? null;
  const model = useMemo(() => (component ? layoutFocus(component) : null), [component]);

  if (isLoading) {
    return (
      <Frame>
        <p className="text-caption text-fg-subtle">Loading the sweep…</p>
      </Frame>
    );
  }
  if (error || !data || !data.available) {
    return (
      <Frame>
        <div className="max-w-[60ch] rounded-lg border border-dashed border-border bg-surface p-5">
          <div className="text-[15px] font-bold">The sweep has not been run</div>
          <p className="mt-2 text-[12.5px] leading-relaxed text-fg-muted">
            Run <code className="font-mono text-accent">python -m sentinel explore rings</code> to
            write <code className="font-mono">exploration/rings.json</code>. This screen reads that
            artefact rather than recomputing it, so the page and the terminal cannot disagree.
          </p>
        </div>
      </Frame>
    );
  }

  const isRing = data.rings_found > 0;
  const device = component?.devices[0] ?? "";
  const confirmed = new Set(component?.confirmed_fraud_cards ?? []);
  const seeds = new Set(component?.seeds ?? []);

  return (
    <div className="absolute inset-x-0 bottom-0 top-0 z-view flex bg-bg">
      <div className="bg-dotgrid relative min-w-0 flex-1 border-r border-border bg-surface">
        {model && component ? (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="absolute inset-0 h-full w-full"
            role="img"
            aria-label={`One device profile shared by ${component.cards.length} cards across ${component.customers.length} customers`}
          >
            {model.edges.map((to) => {
              const hub = model.dots[0];
              const dot = model.dots[to];
              if (!hub || !dot) return null;
              return (
                <line
                  key={to}
                  x1={hub.x}
                  y1={hub.y}
                  x2={dot.x}
                  y2={dot.y}
                  stroke={toneVar(dot.tone)}
                  strokeOpacity="0.4"
                  strokeWidth="2"
                  strokeDasharray="7 7"
                  className="animate-trace"
                />
              );
            })}

            {/* The scope, closing on the shared profile. */}
            <circle
              cx={CX}
              cy={CY}
              r={78}
              fill="none"
              stroke="var(--danger)"
              strokeWidth="1.4"
              strokeDasharray="7 9"
              opacity="0.55"
              className="animate-spin-slow"
            />

            {model.dots.map((dot, index) => (
              <g key={index} className="animate-s-pop">
                <circle
                  cx={dot.x}
                  cy={dot.y}
                  r={dot.r}
                  fill={`color-mix(in srgb, ${toneVar(dot.tone)} 15%, transparent)`}
                  stroke={toneVar(dot.tone)}
                  strokeWidth={dot.hub ? 3 : 2.4}
                />
              </g>
            ))}

            {/* Labels last, so no node can paint over a name. */}
            {model.dots.map((dot, index) => (
              <g key={`label-${index}`}>
                <text
                  x={dot.x}
                  y={dot.y + dot.r + 20}
                  fill="var(--fg)"
                  fontFamily="var(--font-mono)"
                  fontSize={dot.hub ? 16 : 14}
                  fontWeight="600"
                  textAnchor="middle"
                  paintOrder="stroke"
                  stroke="var(--surface)"
                  strokeWidth="4"
                  strokeLinejoin="round"
                >
                  {dot.label}
                </text>
                <text
                  x={dot.x}
                  y={dot.y + dot.r + 37}
                  fill="var(--fg-subtle)"
                  fontFamily="var(--font-mono)"
                  fontSize="12.5"
                  textAnchor="middle"
                  paintOrder="stroke"
                  stroke="var(--surface)"
                  strokeWidth="3.5"
                  strokeLinejoin="round"
                >
                  {dot.sub}
                </text>
              </g>
            ))}
          </svg>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center p-10 text-center">
            <p className="max-w-[46ch] font-mono text-[15px] leading-relaxed text-fg-muted">
              No two seeds share a device at one hop, and two hops turned up no profile worth
              drawing either.
            </p>
          </div>
        )}
      </div>

      <div className="w-[380px] shrink-0 overflow-y-auto bg-surface px-4 pb-7 pt-4">
        <div className="text-[15px] font-bold leading-tight">
          {isRing ? "Undocumented ring" : "No ring on this pack"}
        </div>
        <div className="mt-1.5 font-mono text-[10px] text-fg-muted">
          device projection · {data.hops} hop test · {data.window_days}-day window
        </div>

        <p className="mt-2.5 text-[12px] leading-relaxed text-fg-muted">
          At one hop none of the {data.seeds} seeds shares a device profile with another. That is
          the finding, and R6 is right not to fire.
          {component && !isRing
            ? " Drawn instead is the nearest thing the sweep saw: at two hops, one fingerprint" +
              " carries the cards listed below. It meets the same component test — multi-customer," +
              " bounded, with confirmed fraud already on file — but not at the hop the rule is" +
              " defined on, so it is not counted as a ring."
            : ""}
        </p>

        {component ? (
          <div className="mt-3.5 grid grid-cols-2 gap-2">
            <StatTile label="Cards" value={String(component.cards.length)} />
            <StatTile label="Customers" value={String(component.customers.length)} tone="danger" />
            <StatTile
              label="Confirmed fraud"
              value={String(component.confirmed_fraud_cards.length)}
              tone={component.confirmed_fraud_cards.length > 0 ? "danger" : "success"}
              note="already closed by the bank"
            />
            <StatTile label="Window" value={`${data.window_days} d`} />
            <StatTile
              label="Exposure"
              value={money(component.exposure_usd)}
              tone="danger"
              note="on those closed cases"
            />
            <StatTile label="Known pattern" value="none" tone="warning" />
          </div>
        ) : null}

        <div className="mt-2 grid grid-cols-2 gap-2">
          <StatTile
            label="Rings found"
            value={String(data.rings_found)}
            tone="success"
            note="at one hop, past the gate"
          />
          <StatTile
            label="2-hop reach"
            value={data.two_hop_reach.toLocaleString()}
            tone="warning"
            note="one giant component"
          />
        </div>

        <div className="mt-4 rounded-lg border border-warning-tint bg-warning-tint px-3.5 py-3">
          <div
            className="font-mono text-[9.5px] font-semibold uppercase tracking-[0.1em]"
            style={{ color: "var(--warning)" }}
          >
            Method · R6
          </div>
          <p className="mt-2 text-[12.5px] leading-relaxed" style={{ color: "var(--warning)" }}>
            {data.note}
          </p>
        </div>

        {component ? (
          <>
            <Overline className="mt-5 block">Component members</Overline>
            <ul className="mt-2 flex flex-col gap-1.5">
              {component.cards.map((card) => {
                const flagged = seeds.has(card) || confirmed.has(card);
                return (
                  <li
                    key={card}
                    className="flex items-center gap-2.5 rounded-md border border-border px-3 py-2.5"
                  >
                    <Icon
                      name="card"
                      size={13}
                      style={{ color: toneVar(flagged ? "danger" : "accent") }}
                    />
                    <span className="font-mono text-[10.5px] font-semibold">{card}</span>
                    <span className="flex-1" />
                    {seeds.has(card) ? (
                      <ConsoleChip tone="danger">seed</ConsoleChip>
                    ) : confirmed.has(card) ? (
                      <ConsoleChip tone="danger">confirmed</ConsoleChip>
                    ) : (
                      <ConsoleChip tone="accent">member</ConsoleChip>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="mt-2.5 font-mono text-[10px] leading-relaxed text-fg-subtle">
              all on profile {device} · {data.examined.length} profiles examined ·{" "}
              {data.two_hop_customers.toLocaleString()} customers reached at two hops
            </p>
          </>
        ) : null}

        <p className="mt-5 font-mono text-[9px] text-fg-subtle">swept {data.generated_at}</p>
      </div>
    </div>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-x-0 bottom-0 top-0 z-view flex items-center justify-center bg-bg p-8">
      {children}
    </div>
  );
}
