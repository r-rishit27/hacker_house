import type { ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const chip = cva(
  "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-overline uppercase",
  {
    variants: {
      tone: {
        neutral: "",
        accent: "",
        danger: "",
        success: "",
        warning: "",
        authority: "",
      },
      fill: { solid: "", tint: "", outline: "border", ghost: "" },
    },
    compoundVariants: [
      { tone: "neutral", fill: "tint", class: "bg-surface-2 text-fg-muted" },
      { tone: "neutral", fill: "outline", class: "border-border-strong text-fg" },
      { tone: "neutral", fill: "ghost", class: "text-fg-subtle" },
      { tone: "neutral", fill: "solid", class: "bg-fg-muted text-surface" },

      { tone: "accent", fill: "tint", class: "bg-accent-tint text-accent" },
      { tone: "accent", fill: "outline", class: "border-accent text-accent" },
      { tone: "accent", fill: "ghost", class: "text-accent" },
      { tone: "accent", fill: "solid", class: "bg-accent text-accent-contrast" },

      { tone: "danger", fill: "tint", class: "bg-danger-tint text-danger" },
      { tone: "danger", fill: "outline", class: "border-danger text-danger" },
      { tone: "danger", fill: "ghost", class: "text-danger" },
      { tone: "danger", fill: "solid", class: "bg-danger text-danger-contrast" },

      { tone: "success", fill: "tint", class: "bg-success-tint text-success" },
      { tone: "success", fill: "outline", class: "border-success text-success" },
      { tone: "success", fill: "ghost", class: "text-success" },
      { tone: "success", fill: "solid", class: "bg-success text-success-contrast" },

      { tone: "warning", fill: "tint", class: "bg-warning-tint text-warning" },
      { tone: "warning", fill: "outline", class: "border-warning text-warning" },
      { tone: "warning", fill: "ghost", class: "text-warning" },
      { tone: "warning", fill: "solid", class: "bg-warning text-warning-contrast" },

      { tone: "authority", fill: "tint", class: "bg-authority-tint text-authority" },
      { tone: "authority", fill: "outline", class: "border-authority text-authority" },
      { tone: "authority", fill: "ghost", class: "text-authority" },
      { tone: "authority", fill: "solid", class: "bg-authority text-authority-contrast" },
    ],
    defaultVariants: { tone: "neutral", fill: "tint" },
  },
);

export interface ChipProps extends VariantProps<typeof chip> {
  children: ReactNode;
  className?: string;
  title?: string;
}

export function Chip({ tone, fill, className, children, title }: ChipProps) {
  return (
    <span className={cn(chip({ tone, fill }), className)} title={title}>
      {children}
    </span>
  );
}
