"use client";

import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";
import { Spinner } from "./Spinner";

const button = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md",
    "font-[550] transition-colors duration-fast ease-standard",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
    "focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
    "disabled:pointer-events-none disabled:opacity-50",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-contrast hover:opacity-90 active:opacity-80",
        secondary: "border border-border-strong bg-surface text-fg hover:bg-surface-2",
        ghost: "bg-transparent text-fg-muted hover:bg-surface-2 hover:text-fg",
        danger: "bg-danger text-danger-contrast hover:opacity-90",
        /** Reserved for L2 approval affordances. Nothing else is solid violet. */
        authority: "bg-authority text-authority-contrast hover:opacity-90",
      },
      size: { sm: "h-7 px-2.5 text-caption", md: "h-9 px-3.5 text-body" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, loading, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(button({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Spinner /> : children}
    </button>
  );
});
