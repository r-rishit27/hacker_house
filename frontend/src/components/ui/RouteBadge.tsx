import type { Route } from "@/lib/generated/contract";
import { APPROVERS } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

const EXPLANATION: Record<Route, string> = {
  auto: "The agent may execute this without approval",
  L1: "A team lead must approve",
  L2: "A fraud manager must approve",
};

const STYLE: Record<Route, string> = {
  auto: "text-fg-subtle",
  L1: "border border-border-strong text-fg",
  /** The only solid-violet element in the product. When an analyst sees it,
   *  the action is out of their hands. */
  L2: "border border-authority bg-authority text-authority-contrast",
};

function Chevron({ count }: { count: 1 | 2 }) {
  return (
    <svg viewBox="0 0 12 8" className="h-2 w-3" fill="none" aria-hidden>
      <path d="M1 5.5 4 2.5l3 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      {count === 2 && (
        <path d="M5 5.5 8 2.5l3 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  );
}

export function RouteBadge({ route, className }: { route: Route; className?: string }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-mono text-overline", STYLE[route], className)}
      title={`${EXPLANATION[route]} (${APPROVERS[route].join(", ")})`}
    >
      {route === "L1" && <Chevron count={1} />}
      {route === "L2" && <Chevron count={2} />}
      {route}
      <span className="sr-only"> — {EXPLANATION[route]}</span>
    </span>
  );
}
