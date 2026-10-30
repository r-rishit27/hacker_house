import type { CaseStatus } from "@/lib/generated/contract";
import { Chip } from "./Chip";

const CONFIG = {
  open: { tone: "neutral", fill: "outline", label: "open" },
  escalated: { tone: "warning", fill: "outline", label: "escalated" },
  closed_fraud: { tone: "danger", fill: "tint", label: "closed · fraud" },
  closed_legitimate: { tone: "success", fill: "tint", label: "closed · legitimate" },
} as const;

export function StatusChip({ status }: { status: CaseStatus }) {
  const c = CONFIG[status];
  return (
    <Chip tone={c.tone} fill={c.fill}>
      {status === "escalated" && <span aria-hidden>{"↑"}</span>}
      {c.label}
    </Chip>
  );
}
