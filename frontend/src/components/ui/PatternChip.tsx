import type { Pattern } from "@/lib/generated/contract";
import { Chip } from "./Chip";
import { cn } from "@/lib/cn";

const LABEL: Record<Pattern, string> = {
  card_testing: "card testing",
  card_not_present_fraud: "CNP fraud",
  card_not_present_new_device: "CNP · new device",
  out_of_region_use: "out of region",
  account_takeover: "account takeover",
  undocumented: "undocumented",
  none: "no pattern",
};

export function PatternChip({ pattern, description }: { pattern: Pattern; description?: string }) {
  const isUndocumented = pattern === "undocumented";
  return (
    <Chip
      tone={pattern === "none" ? "neutral" : "accent"}
      fill={isUndocumented ? "outline" : "tint"}
      className={cn(isUndocumented && "border-dashed")}
      title={isUndocumented ? description : undefined}
    >
      {LABEL[pattern]}
    </Chip>
  );
}
