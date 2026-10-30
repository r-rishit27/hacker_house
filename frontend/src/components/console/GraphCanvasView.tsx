"use client";

/**
 * The case as a subgraph.
 *
 * This is the one claim the whole project rests on — that the answer came out
 * of a graph — and until now it was the one claim the console asked you to
 * take on trust. The endpoint was built and tested; nothing drew it.
 *
 * It is drawn rather than laid out by a physics engine. The payloads are
 * small (three to twelve nodes) and a force simulation on twelve nodes is
 * both slower and less legible than a breadth-first ring: the alerted card in
 * the middle, everything it touches on the first ring, everything one hop
 * further on the second. The same case always draws the same picture, which
 * matters when two people are looking at it on a call.
 *
 * Colour is kind, not judgement, with one exception: a node on
 * `affected_txn_ids` is ringed in danger, because "which of these is the
 * agent actually accusing" is the first question anyone asks.
 */

import { useMemo } from "react";

import { Icon } from "@/components/console/icons";
import { toneVar, type Tone } from "@/components/console/vm";
import type { GraphCanvas, GraphNode, GraphNodeKind } from "@/lib/generated/contract";

const W = 560;
const H = 236;
const CX = W / 2;
const CY = H / 2;
/** Ring radii by hop distance from the focus. Two hops is all the API returns. */
const RADII = [0, 74, 128];

/** Kind is hue. Exhaustive over the contract, so a new kind fails the build. */
const KIND_TONE: Record<GraphNodeKind, Tone> = {
  Customer: "accent",
  Card: "accent",
  Transaction: "neutral",
  DeviceProfile: "danger",
  BillingRegion: "neutral",
  EmailDomain: "neutral",
  ProductCode: "neutral",
  ClosedCase: "info",
  FraudCase: "info",
  Alert: "warning",
  PolicyDoc: "info",
};

/** Kind is also size: the things a case is about are drawn bigger. */
const KIND_RADIUS: Record<GraphNodeKind, number> = {
  Customer: 12,
  Card: 15,
  Transaction: 8,
  DeviceProfile: 15,
  BillingRegion: 11,
  EmailDomain: 11,
  ProductCode: 10,
  ClosedCase: 11,
  FraudCase: 13,
  Alert: 12,
  PolicyDoc: 11,
};

/**
 * What fits under a node.
 *
 * A device profile's label is its whole fingerprint — `iOS Device | iOS
 * 11.2.1 | mobile safari 11.0 | 2208x1242` — which is 56 characters and runs
 * off both sides of a 560-unit canvas. The first segment is the part that
 * identifies it; the rest is in the tooltip, where it can be read without
 * destroying the drawing.
 */
const MAX_LABEL = 16;

function shortLabel(label: string): string {
  const head = (label.split("|")[0] ?? label).trim();
  return head.length > MAX_LABEL ? `${head.slice(0, MAX_LABEL - 1)}…` : head;
}

function toneFor(node: GraphNode): Tone {
  if (node.affected) return "danger";
  return KIND_TONE[node.kind];
}

interface Placed {
  node: GraphNode;
  x: number;
  y: number;
  r: number;
  tone: Tone;
  hop: number;
}

/**
 * Breadth-first rings from the focus node.
 *
 * Ordering within a ring is by kind then id so the layout is a pure function
 * of the payload: the same case never rearranges itself between two loads.
 */
function layout(canvas: GraphCanvas): { placed: Placed[]; index: Map<string, Placed> } {
  const nodes = canvas.nodes;
  const neighbours = new Map<string, string[]>();
  for (const node of nodes) neighbours.set(node.id, []);
  for (const edge of canvas.edges) {
    neighbours.get(edge.source)?.push(edge.target);
    neighbours.get(edge.target)?.push(edge.source);
  }

  const root = nodes.find((node) => node.focus) ?? nodes[0];
  const hop = new Map<string, number>();
  if (root) {
    hop.set(root.id, 0);
    let frontier = [root.id];
    let depth = 0;
    while (frontier.length > 0 && depth < RADII.length - 1) {
      depth += 1;
      const next: string[] = [];
      for (const id of frontier) {
        for (const other of neighbours.get(id) ?? []) {
          if (!hop.has(other)) {
            hop.set(other, depth);
            next.push(other);
          }
        }
      }
      frontier = next;
    }
  }
  // Anything the walk never reached — the API can return a node whose edge was
  // truncated — sits on the outer ring rather than vanishing.
  const outer = RADII.length - 1;
  for (const node of nodes) if (!hop.has(node.id)) hop.set(node.id, outer);

  const byHop = new Map<number, GraphNode[]>();
  for (const node of nodes) {
    const depth = hop.get(node.id) ?? outer;
    const bucket = byHop.get(depth) ?? [];
    bucket.push(node);
    byHop.set(depth, bucket);
  }

  const placed: Placed[] = [];
  for (const [depth, bucket] of [...byHop.entries()].sort((a, b) => a[0] - b[0])) {
    const sorted = [...bucket].sort(
      (a, b) => a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id),
    );
    const radius = RADII[Math.min(depth, RADII.length - 1)] ?? 0;
    sorted.forEach((node, index) => {
      // Start at the top and step round. The half-turn offset on odd rings
      // stops a first-ring node from hiding a second-ring one behind it.
      const step = (Math.PI * 2) / Math.max(sorted.length, 1);
      const angle = -Math.PI / 2 + index * step + (depth % 2 === 0 ? 0 : step / 2);
      placed.push({
        node,
        x: radius === 0 ? CX : CX + Math.cos(angle) * radius * 1.55,
        y: radius === 0 ? CY : CY + Math.sin(angle) * radius * 0.78,
        r: KIND_RADIUS[node.kind],
        tone: toneFor(node),
        hop: depth,
      });
    });
  }

  return { placed, index: new Map(placed.map((item) => [item.node.id, item])) };
}

export function GraphCanvasView({
  canvas,
  live,
}: {
  canvas: GraphCanvas | null;
  live: boolean;
}) {
  const model = useMemo(() => (canvas ? layout(canvas) : null), [canvas]);

  if (!canvas || !model || canvas.nodes.length === 0) {
    return (
      <div className="mx-3 rounded-lg border border-dashed border-border bg-surface px-3 py-8 text-center text-caption text-fg-subtle">
        No subgraph for this case yet.
      </div>
    );
  }

  const focus = model.placed.find((item) => item.node.focus) ?? model.placed[0];

  return (
    <div className="mx-3 overflow-hidden rounded-lg border border-border bg-surface">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block h-[172px] w-full"
        role="img"
        aria-label={`Case subgraph: ${canvas.shown} of ${canvas.total} nodes`}
      >
        {canvas.edges.map((edge) => {
          const a = model.index.get(edge.source);
          const b = model.index.get(edge.target);
          if (!a || !b) return null;
          return (
            <line
              key={edge.id}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={edge.affected ? "var(--danger)" : live ? "var(--accent-bold)" : "var(--border)"}
              strokeWidth={edge.affected ? 2 : 1.6}
              strokeDasharray={live ? "6 6" : undefined}
              className={live ? "animate-trace" : undefined}
            >
              <title>{edge.label || edge.type}</title>
            </line>
          );
        })}

        {focus ? (
          <>
            <circle
              cx={focus.x}
              cy={focus.y}
              r={focus.r + 7}
              fill="none"
              stroke={live ? "var(--accent-bold)" : toneVar(focus.tone)}
              strokeWidth="2"
              className="animate-lock"
            />
            <circle
              cx={focus.x}
              cy={focus.y}
              r={focus.r + 14}
              fill="none"
              stroke={live ? "var(--accent-bold)" : toneVar(focus.tone)}
              strokeWidth="1.2"
              strokeDasharray="5 7"
              opacity={live ? 0.85 : 0.35}
              className="animate-spin-slow"
            />
          </>
        ) : null}

        {model.placed.map((item) => (
          <g key={item.node.id} className="animate-s-pop">
            <circle
              cx={item.x}
              cy={item.y}
              r={item.r}
              fill={`color-mix(in srgb, ${toneVar(item.tone)} 20%, transparent)`}
              stroke={toneVar(item.tone)}
              strokeWidth="2"
            />
            <text
              x={item.x}
              y={item.y + item.r + 12}
              fill="var(--fg-muted)"
              fontFamily="var(--font-mono)"
              fontSize="9"
              fontWeight="600"
              textAnchor="middle"
            >
              {shortLabel(item.node.label)}
            </text>
            <title>
              {item.node.kind} {item.node.id}
              {item.node.affected ? " · named in the answer" : ""}
            </title>
          </g>
        ))}
      </svg>

      <div className="flex flex-wrap items-center gap-2.5 border-t border-border px-3 py-2">
        {canvas.legend.map((entry) => (
          <span
            key={entry.kind}
            className="flex items-center gap-1.5 font-mono text-[9px] font-medium text-fg-muted"
          >
            <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
              <circle cx="4" cy="4" r="4" fill={toneVar(KIND_TONE[entry.kind])} />
            </svg>
            {shortLabel(entry.label.split("(")[0] ?? entry.label)} {entry.count}
          </span>
        ))}
        {canvas.truncated ? (
          <span className="ml-auto flex items-center gap-1 font-mono text-[9px] text-fg-subtle">
            <Icon name="alert" size={9} />
            {canvas.shown} of {canvas.total} shown
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** The one-line count the pane's header shows. */
export function canvasSummary(canvas: GraphCanvas | null): string {
  if (!canvas) return "—";
  return `${canvas.nodes.length} nodes · ${canvas.edges.length} edges`;
}
