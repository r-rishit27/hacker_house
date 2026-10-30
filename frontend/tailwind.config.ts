import type { Config } from "tailwindcss";

/**
 * Every value here resolves to a CSS custom property defined in src/app/tokens.css.
 * A hex literal in this file, or in any component, is a review rejection.
 * See DESIGN_SYSTEM.md §7.
 */
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: {
          DEFAULT: "var(--surface)",
          2: "var(--surface-2)",
          3: "var(--surface-3)",
        },
        border: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
        },
        fg: {
          DEFAULT: "var(--fg)",
          muted: "var(--fg-muted)",
          subtle: "var(--fg-subtle)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          tint: "var(--accent-tint)",
          contrast: "var(--accent-contrast)",
          /* Decoration only — it does not meet 3:1. See tokens.css. */
          bold: "var(--accent-bold)",
        },
        danger: {
          DEFAULT: "var(--danger)",
          tint: "var(--danger-tint)",
          contrast: "var(--danger-contrast)",
        },
        success: {
          DEFAULT: "var(--success)",
          tint: "var(--success-tint)",
          contrast: "var(--success-contrast)",
        },
        warning: {
          DEFAULT: "var(--warning)",
          tint: "var(--warning-tint)",
          contrast: "var(--warning-contrast)",
        },
        info: {
          DEFAULT: "var(--info)",
          tint: "var(--info-tint)",
          contrast: "var(--info-contrast)",
        },
        txn: "var(--txn)",
        authority: {
          DEFAULT: "var(--authority)",
          tint: "var(--authority-tint)",
          contrast: "var(--authority-contrast)",
        },
        meter: {
          0: "var(--meter-0)",
          1: "var(--meter-1)",
          2: "var(--meter-2)",
          3: "var(--meter-3)",
          track: "var(--meter-track)",
        },
      },
      borderColor: { DEFAULT: "var(--border)" },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        full: "var(--radius-full)",
      },
      boxShadow: {
        1: "var(--elev-1)",
        2: "var(--elev-2)",
        3: "var(--elev-3)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "-apple-system", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        overline: ["11px", { lineHeight: "14px", letterSpacing: "0.08em", fontWeight: "600" }],
        caption: ["12px", { lineHeight: "16px", fontWeight: "500" }],
        small: ["13px", { lineHeight: "18px" }],
        body: ["14px", { lineHeight: "20px" }],
        "body-strong": ["14px", { lineHeight: "20px", fontWeight: "550" }],
        mono: ["13px", { lineHeight: "18px" }],
        "mono-sm": ["12px", { lineHeight: "16px" }],
        h3: ["16px", { lineHeight: "24px", letterSpacing: "-0.005em", fontWeight: "600" }],
        h2: ["20px", { lineHeight: "28px", letterSpacing: "-0.01em", fontWeight: "600" }],
        h1: ["24px", { lineHeight: "32px", letterSpacing: "-0.015em", fontWeight: "600" }],
        display: ["30px", { lineHeight: "36px", letterSpacing: "-0.02em", fontWeight: "600" }],
      },
      zIndex: {
        sticky: "var(--z-sticky)",
        view: "var(--z-view)",
        dropdown: "var(--z-dropdown)",
        drawer: "var(--z-drawer)",
        dialog: "var(--z-dialog)",
        toast: "var(--z-toast)",
        tooltip: "var(--z-tooltip)",
      },
      transitionTimingFunction: {
        standard: "var(--ease-standard)",
        exit: "var(--ease-exit)",
      },
      transitionDuration: {
        fast: "var(--motion-fast)",
        base: "var(--motion-base)",
        slow: "var(--motion-slow)",
        stream: "var(--motion-stream)",
      },
    },
  },
  plugins: [],
} satisfies Config;
