/**
 * The icon sprite.
 *
 * One `<symbol>` per glyph, defined once in the document and referenced by
 * `<use>`, so a queue of twenty rows costs twenty references rather than
 * twenty copies of the path data.
 *
 * Every glyph is drawn on a 24-unit grid with a 1.7 stroke and `currentColor`,
 * so an icon takes its meaning-colour from the text beside it and never
 * carries a colour of its own. Ported from the console design study.
 */

import type { CSSProperties } from "react";

export type IconName =
  | "scope"
  | "baseline"
  | "window"
  | "device"
  | "region"
  | "ring"
  | "memory"
  | "policy"
  | "recur"
  | "ask"
  | "check"
  | "alert"
  | "card"
  | "user"
  | "bolt"
  | "mail"
  | "doc"
  | "hex";

/** Rendered once, by the console shell. Everything else references it. */
export function IconSprite() {
  return (
    <svg width="0" height="0" className="absolute" aria-hidden="true">
      <defs>
        <symbol id="i-scope" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="1.7" />
          <circle cx="12" cy="12" r="2.4" fill="currentColor" />
          <path d="M12 1.5v4M12 18.5v4M1.5 12h4M18.5 12h4" stroke="currentColor" strokeWidth="1.7" />
        </symbol>
        <symbol id="i-baseline" viewBox="0 0 24 24">
          <rect x="3" y="13" width="4" height="8" rx="1" fill="currentColor" />
          <rect x="10" y="8" width="4" height="13" rx="1" fill="currentColor" />
          <rect x="17" y="15" width="4" height="6" rx="1" fill="currentColor" />
          <path d="M2 5h20" stroke="currentColor" strokeWidth="1.6" strokeDasharray="3 3" />
        </symbol>
        <symbol id="i-window" viewBox="0 0 24 24">
          <rect
            x="2.5"
            y="6"
            width="19"
            height="12"
            rx="2"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <path d="M7 10v4M11 9v6M15 11v3M19 10v4" stroke="currentColor" strokeWidth="1.7" />
        </symbol>
        <symbol id="i-device" viewBox="0 0 24 24">
          <rect
            x="6"
            y="2.5"
            width="12"
            height="19"
            rx="2.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <path d="M10 18.5h4" stroke="currentColor" strokeWidth="1.7" />
          <circle cx="12" cy="9" r="2" fill="currentColor" />
        </symbol>
        <symbol id="i-region" viewBox="0 0 24 24">
          <path
            d="M12 2.5 21.5 12 12 21.5 2.5 12z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <circle cx="12" cy="12" r="2.4" fill="currentColor" />
        </symbol>
        <symbol id="i-ring" viewBox="0 0 24 24">
          <circle cx="12" cy="5" r="3" fill="currentColor" />
          <circle cx="5" cy="17" r="3" fill="currentColor" />
          <circle cx="19" cy="17" r="3" fill="currentColor" />
          <path d="M12 8 6.5 14.5M12 8l5.5 6.5M8 17h8" stroke="currentColor" strokeWidth="1.5" />
        </symbol>
        <symbol id="i-memory" viewBox="0 0 24 24">
          <rect
            x="3"
            y="4"
            width="18"
            height="5"
            rx="1.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <rect x="3" y="11" width="18" height="5" rx="1.5" fill="currentColor" opacity=".55" />
          <rect x="3" y="18" width="18" height="3" rx="1.5" fill="currentColor" opacity=".3" />
        </symbol>
        <symbol id="i-policy" viewBox="0 0 24 24">
          <path
            d="M12 2.5 20 6v6.5c0 4.6-3.4 7.7-8 9-4.6-1.3-8-4.4-8-9V6z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <path
            d="M8.5 12.2l2.6 2.6 4.6-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
          />
        </symbol>
        <symbol id="i-recur" viewBox="0 0 24 24">
          <path
            d="M4 12a8 8 0 0 1 13.7-5.6M20 12a8 8 0 0 1-13.7 5.6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          />
          <path
            d="M18 2.5V7h-4.5M6 21.5V17h4.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          />
        </symbol>
        <symbol id="i-ask" viewBox="0 0 24 24">
          <path
            d="M3.5 5.5h17v11h-9L6 21v-4.5H3.5z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <circle cx="12" cy="11" r="1.4" fill="currentColor" />
          <circle cx="7.5" cy="11" r="1.4" fill="currentColor" />
          <circle cx="16.5" cy="11" r="1.4" fill="currentColor" />
        </symbol>
        <symbol id="i-check" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" strokeWidth="1.7" />
          <path d="M7.8 12.4l2.9 2.9 5.6-6.2" fill="none" stroke="currentColor" strokeWidth="2" />
        </symbol>
        <symbol id="i-alert" viewBox="0 0 24 24">
          <path
            d="M12 3 22 20H2z"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinejoin="round"
          />
          <path d="M12 9.5v4.5" stroke="currentColor" strokeWidth="2" />
          <circle cx="12" cy="17" r="1.2" fill="currentColor" />
        </symbol>
        <symbol id="i-card" viewBox="0 0 24 24">
          <rect
            x="2.5"
            y="5"
            width="19"
            height="14"
            rx="2.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <path d="M2.5 9.5h19" stroke="currentColor" strokeWidth="2.2" />
          <path d="M6 15h4" stroke="currentColor" strokeWidth="1.7" />
        </symbol>
        <symbol id="i-user" viewBox="0 0 24 24">
          <circle cx="12" cy="8" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.7" />
          <path d="M4.5 20.5a7.5 7.5 0 0 1 15 0" fill="none" stroke="currentColor" strokeWidth="1.7" />
        </symbol>
        <symbol id="i-bolt" viewBox="0 0 24 24">
          <path d="M13 2 4 13.5h6L11 22l9-11.5h-6z" fill="currentColor" />
        </symbol>
        <symbol id="i-mail" viewBox="0 0 24 24">
          <rect
            x="2.5"
            y="5"
            width="19"
            height="14"
            rx="2"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
          <path d="M3 7l9 6.5L21 7" fill="none" stroke="currentColor" strokeWidth="1.7" />
        </symbol>
        <symbol id="i-doc" viewBox="0 0 24 24">
          <path d="M6 2.5h8l4.5 4.5v14.5H6z" fill="none" stroke="currentColor" strokeWidth="1.7" />
          <path
            d="M14 2.5V7h4.5M9 12h6M9 16h6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
          />
        </symbol>
        <symbol id="i-hex" viewBox="0 0 24 24">
          <path d="M12 1.8 21 7v10l-9 5.2L3 17V7z" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M12 7.2 16.5 9.8v5.2L12 17.6 7.5 15V9.8z" fill="currentColor" />
        </symbol>
      </defs>
    </svg>
  );
}

export function Icon({
  name,
  size = 13,
  className,
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  /** Only ever a `color`, and only ever a `var(--token)`. See vm.ts. */
  style?: CSSProperties;
}) {
  return (
    <svg width={size} height={size} className={className} style={style} aria-hidden="true">
      <use href={`#i-${name}`} />
    </svg>
  );
}
