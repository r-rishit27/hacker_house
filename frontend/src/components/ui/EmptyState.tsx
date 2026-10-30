/** What a list says when it has nothing, which is a state and not a gap. */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
      <p className="text-body font-semibold text-fg">{title}</p>
      {hint ? <p className="mt-1 text-small text-fg-muted">{hint}</p> : null}
    </div>
  );
}

/** A row-shaped placeholder, so a loading list keeps the page's height. */
export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="h-10 animate-pulse rounded-md bg-surface-2" />
      ))}
    </div>
  );
}

/** A problem, rendered as the API described it rather than as a stack trace. */
export function ErrorNote({ title, detail }: { title: string; detail: string }) {
  return (
    <div role="alert" className="rounded-lg border border-danger bg-danger-tint p-4">
      <p className="text-body font-semibold text-danger">{title}</p>
      <p className="mt-1 text-small text-fg">{detail}</p>
    </div>
  );
}
