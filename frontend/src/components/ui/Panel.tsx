import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface PanelProps {
  title?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  inset?: boolean;
}

export function Panel({ title, eyebrow, actions, children, className, inset }: PanelProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-border",
        inset ? "bg-surface-2" : "bg-surface shadow-1",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
          <div className="min-w-0">
            {eyebrow && <p className="text-overline uppercase text-fg-subtle">{eyebrow}</p>}
            {title && <h2 className="truncate text-h3 text-fg">{title}</h2>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}
