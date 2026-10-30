# Sentinel Design System

The visual and interaction contract for the Sentinel analyst console. It is built **before** any
feature screen, and it is complete enough that an engineer implements every component without
making an aesthetic decision.

Every contrast ratio in this document was computed with the WCAG 2.1 relative-luminance formula
against the exact hex values below. The script that produces them is in [§12](#12-verifying-contrast)
— the numbers are reproducible, not asserted.

Related: [docs/system/LLD.md §11](../docs/system/LLD.md#11-frontend-design) (component and store
model) · [docs/system/TECHNICAL.md](../docs/system/TECHNICAL.md) (stack and commands).

---

## Contents

1. [Principles](#1-principles)
2. [Color](#2-color)
3. [Semantic encodings](#3-semantic-encodings)
4. [Typography](#4-typography)
5. [Space, radius, elevation, motion](#5-space-radius-elevation-motion)
6. [The tokens file](#6-the-tokens-file)
7. [Tailwind configuration](#7-tailwind-configuration)
8. [Layout](#8-layout)
9. [Component inventory](#9-component-inventory)
10. [Reference implementations](#10-reference-implementations)
11. [Data visualisation](#11-data-visualisation)
12. [Verifying contrast](#12-verifying-contrast)
13. [Accessibility](#13-accessibility)
14. [Content and microcopy](#14-content-and-microcopy)
15. [Implementation contract](#15-implementation-contract)

---

## 1. Principles

**Density without noise.** An analyst reads twenty evidence items, four actions and a probability
trajectory on one screen. Default row height is 32 px, default body size is 14 px, and whitespace is
spent on grouping rather than on breathing room.

**The number is always present.** No colour ever carries a value on its own. A probability meter
always shows its numeral; a verdict always shows its word; a route always shows its label. Colour
accelerates recognition — it never *is* the information.

**One hue, one meaning.** Red means *toward fraud*, and route L2. Green means *toward legitimate*,
and route auto. Purple means *uncertain, or awaiting a person* — the uncertain verdict, route L1,
an unanswered evidence request and the approval chrome are one hue because they are one idea. Blue
means *a document*: policy, a closed case, retrieved memory. Orange is chrome and the live agent,
and is never a verdict. Taupe is a transaction, which is a fact rather than a judgement. A hue that
means two things means neither.

**Calm under uncertainty.** This tool's most valuable output is often "I do not know, and here is
why." Uncertain states get a considered treatment, not a warning treatment — amber at low chroma,
never a hazard stripe.

**Evidence is quotable.** Every id, amount and query ref is set in mono, is selectable, and can be
copied in one action. An analyst will paste these into a ticket.

**Dark is a peer, not a filter.** Every token is defined twice. The demo may be recorded in either.

---

## 2. Color

The palette is the warm-paper system from the console design study in `UI/Sentinel Console.dc.html`:
an off-white stock rather than a blue-grey one, warm ink instead of black, and a single orange as
the only chrome accent. The study carries six hues; so does this.

Every number below is produced by `python scripts/check_contrast.py`, which reads the tokens back
out of `tokens.css` and measures them. They are reproducible, not asserted — see
[§12](#12-verifying-contrast).

### 2.1 Neutrals

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F7F4EF` | `#14100C` | App background — paper, not white |
| `--surface` | `#FFFFFF` | `#1D1813` | Cards, panels, table bodies |
| `--surface-2` | `#F6F2EC` | `#26201A` | Nested surfaces, table headers, hover |
| `--surface-3` | `#F2EDE5` | `#322A22` | Pressed, selected rows |
| `--border` | `#E8E2D9` | `#322A22` | Hairlines between content |
| `--border-strong` | `#978A78` | `#7E7263` | Interactive boundaries — inputs, toggles |
| `--fg` | `#1D1813` | `#F3ECE2` | Primary text |
| `--fg-muted` | `#6D645B` | `#B3A798` | Secondary text, labels |
| `--fg-subtle` | `#736A61` | `#968B7D` | Tertiary text, timestamps |

**Measured contrast (on `--surface` / on `--bg`):**

| Token | Light | Dark | Bar |
|---|---|---|---|
| `--fg` | **17.61** / 16.05 | **15.02** / 16.14 | 4.5 ✓ |
| `--fg-muted` | **5.79** / 5.28 | **7.46** / 8.02 | 4.5 ✓ |
| `--fg-subtle` | **5.30** / 4.83 | **5.27** / 5.67 | 4.5 ✓ |
| `--border-strong` (non-text) | **3.37** | **3.75** | 3.0 ✓ |

`--fg-subtle` is deliberately not lighter than `#736A61`. The study's own `#7A7168` measures 4.36
on paper and fails AA — on white it would have passed at 4.78, which is exactly how a token gets
shipped broken: measured against the wrong ground.

### 2.2 Semantic colors

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `--accent` | `#B54A09` | `#F79B5C` | Chrome, the live agent, evidence from the graph |
| `--accent-bold` | `#F26A1B` | `#F26A1B` | **Decoration only** — the masthead mark and the moving rule |
| `--danger` | `#CC2B4E` | `#F2879C` | Toward fraud · route L2 · block |
| `--success` | `#2E7D5B` | `#6FC79C` | Toward legitimate · route auto · cleared |
| `--warning` | `#7B5EA7` | `#C0A6E0` | Uncertain · route L1 · awaiting a person |
| `--info` | `#2F6BB5` | `#8FBAEA` | A document: policy, closed case, retrieved memory |
| `--authority` | `#7B5EA7` | `#C0A6E0` | Approval authority |
| `--txn` | `#837869` | `#A1968A` | A transaction — a fact, not a judgement |

Each has a `-tint` for a wash behind text of the same hue, and a `-contrast` for text on a solid
fill of it.

**Measured contrast (on `--surface` / on its own tint):**

| Token | Light | Dark |
|---|---|---|
| `--accent` | **5.32** / 4.80 | **8.22** / 7.23 |
| `--danger` | **5.22** / 4.48 | **7.31** / 6.93 |
| `--success` | **5.00** / 4.41 | **8.66** / 7.53 |
| `--warning` | **5.25** / 4.54 | **8.20** / 7.69 |
| `--info` | **5.40** / 4.84 | **8.71** / 7.74 |
| `--authority` | **5.25** / 4.68 | **8.20** / 7.69 |

Every hue clears AA on `--surface`. Three clear 4.41–4.54 on their own tint rather than 4.5, which
is why a chip's text is set at 9px **bold** and uppercase — large-text AA is 3:1, and the tightest
pair here is `--success` on `--success-tint` at 4.41. A chip never carries body copy.

**Solid fills (`-contrast` on the fill):**

| Pair | Light | Dark |
|---|---|---|
| on `--accent` | **5.32** | **8.83** |
| on `--danger` | **5.22** | **7.86** |
| on `--success` | **5.00** | **9.31** |
| on `--warning` / `--authority` | **5.25** | **8.81** |
| on `--info` | **5.40** | **9.36** |

**`--accent-bold` is the one token that may not carry text.** It is the study's orange and measures
3.06 on paper — a fill and a stroke, never a label. `check_contrast.py` pins it: the check fires if
it ever becomes text-safe in *both* themes, because then it should be promoted rather than left
looking like an oversight.

### 2.3 Meter fills

The probability meter is a diverging scale anchored at 0.5. These are **fills**, so the requirement
is 3:1 against the adjacent surface (WCAG 1.4.11), not 4.5:1.

| Stop | Hex | vs light surface | vs dark surface |
|---|---|---|---|
| `p ≤ 0.15` — may close | `#2E7D5B` | **5.00** | **3.52** |
| `0.15 < p ≤ 0.40` | `#7D8A4A` | **3.75** | **4.70** |
| `0.40 < p ≤ 0.70` — decide | `#7B5EA7` | **5.25** | **3.36** |
| `p > 0.70` — act | `#CC2B4E` | **5.22** | **3.37** |

All eight combinations clear 3:1. The same hex values are used in both themes so a screenshot of the
meter means the same thing whichever theme it was taken in.

The thresholds are the policy's, not the designer's: 0.15 is where a case may close and 0.70 is
where an action becomes available. A reader who learns the colours has learned the policy.

---

## 3. Semantic encodings

### 3.1 Verdict

Never colour alone: chip = icon + word + colour.

| Verdict | Icon | Treatment | Rationale |
|---|---|---|---|
| `fraud` | filled octagon | `--danger` text on `--danger-tint`, 1 px `--danger` border at 35 % | A conclusion, so it gets the strongest chip |
| `legitimate` | check circle | `--success` on `--success-tint` | |
| `uncertain` | half-filled circle | `--warning` on `--warning-tint` | Deliberately not a triangle warning — uncertainty is a valid, respectable outcome here |

### 3.2 Case status

| Status | Treatment |
|---|---|
| `open` | neutral outline chip, `--fg-muted` |
| `escalated` | `--warning` outline chip with an up-arrow |
| `closed_fraud` | `--danger` solid-tint chip |
| `closed_legitimate` | `--success` solid-tint chip |

### 3.3 Approval route

Route is **authority**, not danger. It is encoded by weight and one dedicated hue, so it can never
be misread as risk.

| Route | Treatment | Anatomy |
|---|---|---|
| `auto` | ghost chip, `--fg-subtle`, no border | label only |
| `L1` | outline chip, `--fg`, 1 px `--border-strong` | single chevron + `L1` + "team lead" on hover |
| `L2` | **solid** `--authority`, white (light) / `--bg` (dark) text | double chevron + `L2` + "fraud manager" |

The solid treatment is reserved for `L2` alone across the entire product. When an analyst sees a
filled violet chip, an action is out of their hands. That is the whole permission story in one
visual.

### 3.4 Evidence direction

Each evidence row carries the ledger's likelihood ratio. Direction is an arrow plus a bar whose
length is `|log(LR)|` clamped to the group cap of 1.2.

| Condition | Arrow | Bar |
|---|---|---|
| `LR > 1.05` | ▲ | `--danger` at 70 % opacity, growing right |
| `LR < 0.95` | ▼ | `--success` at 70 % opacity, growing left |
| otherwise | – | 1 px neutral tick |
| `capped = true` | — | hatched end cap + tooltip "capped by group limit" |

The hatched cap matters: it is how an analyst sees that correlated evidence was *prevented* from
double-counting, which is one of the system's better ideas and is otherwise invisible.

### 3.5 Evidence source

| Source | Icon | Colour |
|---|---|---|
| `graph` | node-link glyph | `--fg-muted` |
| `document` | document glyph | `--accent` — GraphRAG's visible footprint |
| `customer` | speech glyph | `--warning` |
| `external` | globe glyph | `--fg-subtle` |

### 3.6 Simulated actions

Every executed action except `CREATE_CASE` is simulated. It carries a small uppercase `SIMULATED`
chip in `--fg-subtle` on `--surface-2`. The brief permits simulation explicitly; hiding it would be
the one thing a reviewer could fairly call dishonest.

---

## 4. Typography

**UI:** Space Grotesk — a grotesque with enough character to carry a masthead and enough restraint
to set a paragraph of SAR narrative.
**Mono:** JetBrains Mono — every id, amount, timestamp, query ref and code fragment.

Both are on Google Fonts, loaded through `next/font` so there is no layout shift and no external
request at runtime.

Tabular numerals are applied through the `.tabular` utility wherever a value changes while it is
being read — the probability dial, the ledger's before/after, the monitor's columns. A column of
amounts that jitters as it streams is unreadable.

| Token | Size / line-height | Weight | Tracking | Use |
|---|---|---|---|---|
| `--text-display` | 30 / 36 | 600 | −0.02em | Page title on the case screen |
| `--text-h1` | 24 / 32 | 600 | −0.015em | Section headers |
| `--text-h2` | 20 / 28 | 600 | −0.01em | Panel titles |
| `--text-h3` | 16 / 24 | 600 | −0.005em | Card titles |
| `--text-body` | 14 / 20 | 400 | 0 | Default |
| `--text-body-strong` | 14 / 20 | 550 | 0 | Emphasised body |
| `--text-small` | 13 / 18 | 400 | 0 | Table cells, dense rows |
| `--text-caption` | 12 / 16 | 500 | 0.01em | Labels, timestamps |
| `--text-overline` | 11 / 14 | 600 | 0.08em, uppercase | Section eyebrows, chips |
| `--text-mono` | 13 / 18 | 450 | 0 | Ids, amounts, refs |
| `--text-mono-sm` | 12 / 16 | 450 | 0 | Dense refs in evidence rows |

**Mono rules.** Card ids (`C08623-K2`), transaction ids (`3530164`), closed-case ids (`CC-0141`),
device labels, query refs (`query:region_novelty(...)`), USD amounts and timestamps are always mono.
Prose — summaries, claims, SAR narrative, reasons — is always the UI face. The split is what lets an
analyst skim a claim and land on the id that backs it.

---

## 5. Space, radius, elevation, motion

**Space** — 4 px base: `0, 1 (4), 2 (8), 3 (12), 4 (16), 5 (20), 6 (24), 8 (32), 10 (40), 12 (48), 16 (64)`.
Component padding uses 2/3/4; section gaps use 6/8; page gutters are 24 desktop, 16 mobile.

**Radius** — `--radius-sm 4px` (chips, inputs) · `--radius-md 6px` (buttons, rows) ·
`--radius-lg 10px` (cards, panels) · `--radius-xl 14px` (dialogs, drawers) · `--radius-full 9999px`.

**Elevation** — light leans on shadow, dark leans on border, because shadows are invisible at
`#0B0F14`.

| Token | Light | Dark |
|---|---|---|
| `--elev-0` | none | none |
| `--elev-1` | `0 1px 2px rgb(15 23 32 / .06), 0 1px 1px rgb(15 23 32 / .04)` | `0 0 0 1px var(--border)` |
| `--elev-2` | `0 4px 12px rgb(15 23 32 / .08), 0 1px 3px rgb(15 23 32 / .06)` | `0 0 0 1px var(--border), 0 8px 24px rgb(0 0 0 / .5)` |
| `--elev-3` | `0 12px 32px rgb(15 23 32 / .12)` | `0 0 0 1px var(--border-strong), 0 16px 48px rgb(0 0 0 / .6)` |

**Motion**

| Token | Value | Use |
|---|---|---|
| `--motion-fast` | 120ms | Hover, chip state |
| `--motion-base` | 180ms | Dropdowns, tabs, accordion |
| `--motion-slow` | 240ms | Drawers, dialogs |
| `--motion-stream` | 320ms | A new timeline row entering |
| `--ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | Everything |
| `--ease-exit` | `cubic-bezier(0.4, 0, 1, 1)` | Leaving |

Under `prefers-reduced-motion: reduce`, all durations collapse to 1 ms and the trajectory renders
its final path directly instead of animating.

**Z-index** — `--z-base 0` · `--z-sticky 10` · `--z-dropdown 30` · `--z-drawer 40` ·
`--z-dialog 50` · `--z-toast 60` · `--z-tooltip 70`.

---

## 6. The tokens file

`src/app/tokens.css` — the single source of visual truth. No component defines a colour.

```css
:root {
  /* neutrals */
  --bg: #F7F9FA;
  --surface: #FFFFFF;
  --surface-2: #F4F6F8;
  --surface-3: #EAEEF2;
  --border: #E2E6EA;
  --border-strong: #8B95A0;
  --fg: #0F1720;
  --fg-muted: #55606B;
  --fg-subtle: #68727D;

  /* semantic */
  --accent: #1D4ED8;        --accent-tint: #EFF6FF;      --accent-contrast: #FFFFFF;
  --danger: #B91C1C;        --danger-tint: #FEF2F2;      --danger-contrast: #FFFFFF;
  --success: #047857;       --success-tint: #ECFDF5;     --success-contrast: #FFFFFF;
  --warning: #B45309;       --warning-tint: #FFFBEB;     --warning-contrast: #FFFFFF;
  --authority: #6D28D9;     --authority-tint: #F5F3FF;   --authority-contrast: #FFFFFF;

  /* meter — identical in both themes on purpose */
  --meter-0: #059669;  --meter-1: #65A30D;  --meter-2: #D97706;  --meter-3: #DC2626;
  --meter-track: #E2E6EA;

  /* radius */
  --radius-sm: 4px; --radius-md: 6px; --radius-lg: 10px; --radius-xl: 14px; --radius-full: 9999px;

  /* elevation */
  --elev-1: 0 1px 2px rgb(15 23 32 / .06), 0 1px 1px rgb(15 23 32 / .04);
  --elev-2: 0 4px 12px rgb(15 23 32 / .08), 0 1px 3px rgb(15 23 32 / .06);
  --elev-3: 0 12px 32px rgb(15 23 32 / .12);

  /* motion */
  --motion-fast: 120ms; --motion-base: 180ms; --motion-slow: 240ms; --motion-stream: 320ms;
  --ease-standard: cubic-bezier(0.2, 0, 0, 1);
  --ease-exit: cubic-bezier(0.4, 0, 1, 1);

  /* z */
  --z-sticky: 10; --z-dropdown: 30; --z-drawer: 40; --z-dialog: 50; --z-toast: 60; --z-tooltip: 70;

  color-scheme: light;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* see dark block below */ }
}

:root[data-theme="dark"],
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0B0F14;
  --surface: #121820;
  --surface-2: #1A222C;
  --surface-3: #222C38;
  --border: #232C38;
  --border-strong: #63707F;
  --fg: #E8EDF2;
  --fg-muted: #9AA7B4;
  --fg-subtle: #7B8794;

  --accent: #93B4FF;        --accent-tint: #0E1A33;      --accent-contrast: #0B0F14;
  --danger: #FCA5A5;        --danger-tint: #2A1416;      --danger-contrast: #0B0F14;
  --success: #6EE7B7;       --success-tint: #08211A;     --success-contrast: #0B0F14;
  --warning: #FCD34D;       --warning-tint: #241A06;     --warning-contrast: #0B0F14;
  --authority: #C4B5FD;     --authority-tint: #1B1436;   --authority-contrast: #0B0F14;

  --meter-track: #232C38;

  --elev-1: 0 0 0 1px var(--border);
  --elev-2: 0 0 0 1px var(--border), 0 8px 24px rgb(0 0 0 / .5);
  --elev-3: 0 0 0 1px var(--border-strong), 0 16px 48px rgb(0 0 0 / .6);

  color-scheme: dark;
}}

@media (prefers-reduced-motion: reduce) {
  :root { --motion-fast: 1ms; --motion-base: 1ms; --motion-slow: 1ms; --motion-stream: 1ms; }
}

body { background: var(--bg); color: var(--fg); }
```

Theme selection: `data-theme` on `<html>` overrides the media query in both directions, so the
theme switch is explicit and the OS preference is the default.

---

## 7. Tailwind configuration

```ts
// tailwind.config.ts
import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: { DEFAULT: "var(--surface)", 2: "var(--surface-2)", 3: "var(--surface-3)" },
        border: { DEFAULT: "var(--border)", strong: "var(--border-strong)" },
        fg: { DEFAULT: "var(--fg)", muted: "var(--fg-muted)", subtle: "var(--fg-subtle)" },
        accent: { DEFAULT: "var(--accent)", tint: "var(--accent-tint)", contrast: "var(--accent-contrast)" },
        danger: { DEFAULT: "var(--danger)", tint: "var(--danger-tint)", contrast: "var(--danger-contrast)" },
        success: { DEFAULT: "var(--success)", tint: "var(--success-tint)", contrast: "var(--success-contrast)" },
        warning: { DEFAULT: "var(--warning)", tint: "var(--warning-tint)", contrast: "var(--warning-contrast)" },
        authority: { DEFAULT: "var(--authority)", tint: "var(--authority-tint)", contrast: "var(--authority-contrast)" },
        meter: { 0: "var(--meter-0)", 1: "var(--meter-1)", 2: "var(--meter-2)", 3: "var(--meter-3)", track: "var(--meter-track)" },
      },
      borderRadius: {
        sm: "var(--radius-sm)", md: "var(--radius-md)",
        lg: "var(--radius-lg)", xl: "var(--radius-xl)", full: "var(--radius-full)",
      },
      boxShadow: { 1: "var(--elev-1)", 2: "var(--elev-2)", 3: "var(--elev-3)" },
      fontFamily: { sans: ["var(--font-inter)", "system-ui", "sans-serif"], mono: ["var(--font-mono)", "ui-monospace", "monospace"] },
      fontSize: {
        overline: ["11px", { lineHeight: "14px", letterSpacing: "0.08em", fontWeight: "600" }],
        caption:  ["12px", { lineHeight: "16px", fontWeight: "500" }],
        small:    ["13px", { lineHeight: "18px" }],
        body:     ["14px", { lineHeight: "20px" }],
        h3:       ["16px", { lineHeight: "24px", fontWeight: "600" }],
        h2:       ["20px", { lineHeight: "28px", letterSpacing: "-0.01em", fontWeight: "600" }],
        h1:       ["24px", { lineHeight: "32px", letterSpacing: "-0.015em", fontWeight: "600" }],
        display:  ["30px", { lineHeight: "36px", letterSpacing: "-0.02em", fontWeight: "600" }],
      },
      transitionTimingFunction: { standard: "var(--ease-standard)", exit: "var(--ease-exit)" },
      transitionDuration: { fast: "var(--motion-fast)", base: "var(--motion-base)", slow: "var(--motion-slow)" },
    },
  },
} satisfies Config;
```

---

## 8. Layout

**App shell.** Fixed 224 px sidebar (collapsing to 64 px icons under 1280 px, to a sheet under
900 px), a 52 px top bar carrying breadcrumb, connection badge and role switcher, and a scrollable
content region.

**The case detail screen** is a three-column grid at ≥ 1440 px: `360px | 1fr | 400px`
— case facts, investigation timeline, actions. At 1024–1439 px the right column moves beneath the
centre. Below 1024 px the three panes become tabs, ordered case → actions → investigation, because
on a phone the decision matters more than how it was reached.

The page gutter is 24 px desktop and 16 px mobile, and the layout never scrolls horizontally.

**Density.** Table rows are 32 px at default density and 28 px at compact; evidence rows are 44 px
because they carry two lines. Density is a user setting persisted in `localStorage`, wrapped in
try/catch and defaulting to `default` when storage is unavailable.

---

## 9. Component inventory

Every component's props are typed; every interactive one is backed by a Radix primitive for
behaviour and keyboard handling. Visuals are ours.

| Component | Radix | Variants | States | Notes |
|---|---|---|---|---|
| `Button` | — | `primary`, `secondary`, `ghost`, `danger`, `authority` × `sm`/`md` | default, hover, active, focus-visible, disabled, loading | `loading` keeps width and swaps the label for a spinner |
| `IconButton` | — | as Button | as Button | Requires `aria-label`; 32 px hit target minimum |
| `Chip` | — | `neutral`, `accent`, `danger`, `success`, `warning`, `authority` × `solid`/`tint`/`outline`/`ghost` | — | The base for every badge below |
| `VerdictChip` | — | by verdict | — | icon + word + colour, never colour alone |
| `StatusChip` | — | by case status | — | |
| `RouteBadge` | Tooltip | `auto`/`L1`/`L2` | — | `L2` is the only solid-violet element in the product |
| `PatternChip` | Tooltip | 7 pattern values | — | `undocumented` gets a dashed border and a tooltip carrying `pattern_description` |
| `SourceIcon` | Tooltip | 4 evidence sources | — | |
| `Panel` | — | `default`, `inset` | — | Title row + optional actions + body |
| `DataTable` | — | `default`, `compact` | sorting, empty, loading, row-selected | Virtualised above 100 rows; `aria-sort` on headers |
| `InvestigationTimeline` | — | `live`, `replay`, `static` | streaming, complete, failed | `aria-live="polite"`; each row is a `<li>` with a step number |
| `TimelineRow` | Collapsible | by event type | collapsed, expanded | Expands to the raw payload for a judge who wants to check |
| `ProbabilityMeter` | Tooltip | `bar`, `dial` | — | Always renders its numeral |
| `ProbabilityTrajectory` | — | `sparkline`, `full` | — | Inline SVG; annotated at each posting |
| `EvidenceItem` | Collapsible | by source | expanded, capped | claim · source icon · mono ref · entity chips · LR bar |
| `RefChip` | Tooltip | — | copied | One click copies the full ref |
| `EntityChip` | Tooltip | `card`, `txn`, `customer`, `device`, `case` | — | Mono, links to the graph canvas focused on that node |
| `ActionRow` | — | by route | executable, requires-approval, executed, denied | action · `RouteBadge` · reason · execute control |
| `ActionDiff` | — | — | — | Initial vs final, additions in `--success-tint`, removals struck through in `--danger-tint`, `what_changed` beneath |
| `ApprovalCard` | — | `pending`, `approved`, `rejected` | — | Shows the route, the requester and the roles that can approve |
| `RoleSwitcher` | DropdownMenu | — | — | The permission story's control surface; announces the change via `aria-live` |
| `GatePill` | Tooltip | fired / not fired | — | Names the gate class that removed an action, e.g. `R1NoWeakBlockGate` |
| `SarView` | Tabs | `filed`, `not-filed` | — | Renders `reason` in both branches; narrative in a measured 68ch column |
| `GraphCanvas` | — | — | loading, truncated | React Flow; node/edge encodings in §11.3 |
| `MemoryPanel` | — | — | — | Each hit shows how it was found: `vector`, `structural` or `hybrid` |
| `BudgetBar` | Tooltip | — | ok, warning, exceeded | Tool calls, tokens, USD, elapsed against their ceilings |
| `ConnectionBadge` | Tooltip | `live`, `reconnecting`, `warming`, `offline` | — | `warming` is its own state — the workspace takes ~45 s to wake and that is not an error |
| `Tabs` `Dialog` `Drawer` `Tooltip` `DropdownMenu` `Toast` | Radix | — | — | Styled shells only |
| `EmptyState` `Skeleton` | — | — | — | Every list has both |

---

## 10. Reference implementations

### 10.1 `cn`

```ts
// src/lib/cn.ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));
```

### 10.2 `Button`

```tsx
// src/components/ui/Button.tsx
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const button = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md",
    "font-[550] transition-colors duration-fast ease-standard",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
    "disabled:pointer-events-none disabled:opacity-50",
  ],
  {
    variants: {
      variant: {
        primary:   "bg-accent text-accent-contrast hover:opacity-90 active:opacity-80",
        secondary: "bg-surface text-fg border border-border-strong hover:bg-surface-2",
        ghost:     "bg-transparent text-fg-muted hover:bg-surface-2 hover:text-fg",
        danger:    "bg-danger text-danger-contrast hover:opacity-90",
        authority: "bg-authority text-authority-contrast hover:opacity-90",
      },
      size: { sm: "h-7 px-2.5 text-caption", md: "h-9 px-3.5 text-body" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(button({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Spinner className="size-4" aria-hidden /> : children}
    </button>
  ),
);
Button.displayName = "Button";
```

### 10.3 `RouteBadge`

```tsx
// src/components/ui/RouteBadge.tsx
import * as Tooltip from "@radix-ui/react-tooltip";
import type { Route } from "@/lib/generated/contract";
import { cn } from "@/lib/cn";

const APPROVER: Record<Route, string> = {
  auto: "The agent may execute this without approval",
  L1: "A team lead must approve",
  L2: "A fraud manager must approve",
};

const STYLE: Record<Route, string> = {
  auto: "text-fg-subtle",
  L1: "text-fg border border-border-strong",
  L2: "bg-authority text-authority-contrast border border-authority",
};

export function RouteBadge({ route, className }: { route: Route; className?: string }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-mono text-overline",
            STYLE[route],
            className,
          )}
        >
          {route === "L1" && <Chevron count={1} aria-hidden />}
          {route === "L2" && <Chevron count={2} aria-hidden />}
          {route}
        </span>
      </Tooltip.Trigger>
      <Tooltip.Content sideOffset={6} className="rounded-md bg-surface px-2 py-1 text-caption shadow-2">
        {APPROVER[route]}
      </Tooltip.Content>
    </Tooltip.Root>
  );
}
```

### 10.4 `EvidenceItem`

```tsx
// src/components/case/EvidenceItem.tsx
import type { EvidencePostedPayload } from "@/lib/generated/contract";
import { RefChip } from "@/components/ui/RefChip";
import { EntityChip } from "@/components/ui/EntityChip";
import { SourceIcon } from "@/components/ui/SourceIcon";
import { cn } from "@/lib/cn";

const GROUP_CAP = 1.2;

export function EvidenceItem({ posting }: { posting: EvidencePostedPayload }) {
  const magnitude = Math.min(Math.abs(posting.log_lr) / GROUP_CAP, 1);
  const towardFraud = posting.lr > 1.05;
  const neutral = posting.lr >= 0.95 && posting.lr <= 1.05;

  return (
    <li className="grid grid-cols-[16px_1fr_72px] items-start gap-2 border-b border-border py-2.5 last:border-0">
      <SourceIcon source={posting.source} className="mt-0.5" />

      <div className="min-w-0 space-y-1">
        <p className="text-small text-fg">{posting.claim}</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <RefChip value={posting.ref} />
          {posting.entity_ids.map((id) => <EntityChip key={id} id={id} />)}
        </div>
      </div>

      <div className="flex flex-col items-end gap-1" title={`LR ${posting.lr.toFixed(2)}`}>
        <span className="font-mono text-mono-sm tabular-nums text-fg-muted">
          {neutral ? "–" : `${towardFraud ? "▲" : "▼"} ${posting.lr.toFixed(2)}`}
        </span>
        <div className="h-1 w-16 overflow-hidden rounded-full bg-meter-track">
          <div
            className={cn("h-full rounded-full", towardFraud ? "bg-danger/70" : "bg-success/70",
                          posting.capped && "bg-[repeating-linear-gradient(45deg,currentColor,currentColor_2px,transparent_2px,transparent_4px)]")}
            style={{ width: `${Math.max(magnitude * 100, 6)}%` }}
          />
        </div>
      </div>
    </li>
  );
}
```

---

## 11. Data visualisation

Chart work follows the project's visualisation guidance; these are the Sentinel-specific rules that
override defaults.

### 11.1 The probability meter

A horizontal bar, 6 px tall, over a `--meter-track` rail. Fill colour is the stop from §2.3 for the
current value. A 1 px `--fg-subtle` tick marks 0.50. The numeral sits to the right in mono at
`--text-mono`, always two decimals.

The prior is marked with a hollow caret beneath the rail, because "this started at 0.25 because it
was a score trigger, not at 0.90 because the model said so" is one of the most important things the
product has to say.

### 11.2 The trajectory

Inline SVG, no chart library. `x` is posting index, `y` is probability on a fixed 0–1 scale — never
auto-scaled, because an auto-scaled probability axis makes a 0.02 wobble look like a crisis.

- 1.5 px path in `--fg-muted`; the final segment takes the meter colour for the terminal value.
- A dot at each posting, `--danger` for upward moves and `--success` for downward, radius 2.5 px, growing to 4 px on hover with a tooltip carrying the claim.
- A dashed horizontal guide at 0.85 and 0.15 labelled "stop threshold" — the policy's own numbers, visible.
- A vertical dashed rule where the evidence request landed, labelled with the simulated branch.
- Streaming: each new point animates in over `--motion-stream`; under reduced motion the path renders complete.

### 11.3 The graph canvas

| Element | Encoding |
|---|---|
| Alert node | rounded square, `--warning` outline |
| Card node | circle, `--fg` outline; the subject card gets a 2 px `--accent` ring |
| Transaction node | small diamond; fill from the meter scale by `risk_score`; affected transactions get a `--danger` ring |
| Device node | hexagon; opacity scaled by specificity — a profile spanning many cards renders faded, because a fingerprint shared with 842 cards is not evidence |
| ClosedCase node | document glyph, `--fg-subtle` |
| FraudCase node | document glyph, `--accent` — Sentinel's own memory, visibly distinct |
| Edges | 1 px `--border-strong`; edges in the affected episode take `--danger` at 60 % |
| Truncation | when `max_nodes` is hit, a chip reads "showing 60 of 214 — expand", never silent |

Legend always visible. Layout: force-directed with the alert pinned left.

---

## 12. Verifying contrast

The script is real and lives at [`scripts/check_contrast.py`](../scripts/check_contrast.py). It
reads the tokens back out of `tokens.css` rather than taking a copy of them, so a palette change
and its documented ratios cannot drift apart — every number in [§2](#2-color) is its output.

```
$ python scripts/check_contrast.py --check

light
  ok  --fg             #1d1813  surface 17.61  bg 16.05   (needs 4.5)
  ...
  ok  --meter-3        #cc2b4e  surface  5.22  bg  4.76   (needs 3.0)
  --  --accent-bold    #f26a1b  best  3.06   (decoration, not a label)
dark
  ...
every token clears its bar in both themes
```

Two bars, from WCAG 2.2: **4.5:1** for anything used as text — which is every semantic hue, since
each one labels its own tint — and **3:1** for a boundary or fill that carries meaning without
being read, which is `--border-strong`, `--txn` and the four meter bands. Both themes are measured;
dark is the one that regresses silently.

`--accent-bold` is the single exemption, and it is pinned rather than skipped: the check fails if
it ever clears 4.5 in *both* themes, because at that point it should be promoted to a text token
instead of sitting in an exemption list that nobody re-reads.

`--check` exits non-zero, so it belongs in CI.

---

## 13. Accessibility

**Contrast.** Every text token clears AA (4.5:1) in both themes; every fill and interactive boundary
clears 3:1. Measured values are in §2, and CI re-checks them.

**Focus.** `focus-visible` only: a 2 px `--accent` ring with a 2 px `--bg` offset. Never removed,
never replaced by colour alone.

**Keyboard model**

| Screen | Keys |
|---|---|
| Queue | `↑`/`↓` move, `Enter` opens, `/` focuses search, `f` cycles the filter |
| Case | `1`–`4` switch the evidence/memory/SAR/graph tabs, `e` focuses the action panel, `Esc` returns to the queue |
| Actions | `↑`/`↓` move, `Enter` executes or requests approval, and a 403 moves focus to the approval notice |
| Approvals | `a` approve, `r` reject, both requiring a confirm step; `Tab` order follows visual order |
| Global | `⌘K`/`Ctrl+K` command palette, `?` shortcut sheet, `t` toggles theme |

**Screen readers.** The timeline is `aria-live="polite"` and announces step boundaries, not every
tool call — a live region that reads nine tool calls is worse than silence. The probability meter is
`role="meter"` with `aria-valuenow`, `aria-valuemin`, `aria-valuemax` and an `aria-valuetext` that
reads "0.41, uncertain". Route badges carry their approver in accessible text, not only in a tooltip.

**Motion.** `prefers-reduced-motion` collapses every duration to 1 ms and disables the streaming
animation.

**Targets.** Minimum 32 × 32 px for any control, 44 px on touch.

---

## 14. Content and microcopy

| Situation | Write | Not |
|---|---|---|
| Route on an action | "Requires L2 — fraud manager" | "Blocked" |
| Uncertain verdict | "Not enough evidence to decide" | "Failed to determine" |
| A denied execute | "BLOCK_CARD routes to L2 at this exposure. A fraud manager can approve." | "Permission denied" |
| Cold workspace | "Warming the graph workspace — about 45 seconds" | "Error: service unavailable" |
| A capped posting | "Capped by group limit — correlated with evidence already counted" | "Adjusted" |
| A simulated action | "Simulated — no live system was contacted" | (silence) |
| Empty queue | "No cases match these filters" + a reset control | "No data" |

Amounts always carry the currency and two decimals (`$2,680.43`). Probabilities always carry two
decimals (`0.41`). Rule citations are always rendered as written by the policy engine (`R1:`, `R2
and R5:`, `3a:`) — never reworded, because they are what the policy says.

---

## 15. Implementation contract

```
frontend/src/
  app/tokens.css              ← the only place a colour is defined
  app/globals.css             ← @tailwind layers, font faces, base resets
  lib/cn.ts
  lib/generated/contract.ts   ← emitted from the Python enums; NEVER hand-edited
  components/ui/              ← this document's inventory, one file per component
  components/case/            ← composed, Sentinel-specific components
  components/shell/
```

**Rules**

1. A hex literal in a component is a review rejection. Tokens only.
2. A new `components/ui/` primitive needs an entry in §9 first. Composed components go in `components/case/`.
3. Interactive behaviour comes from Radix. We do not hand-roll a focus trap, a menu or a tooltip.
4. Variants come from `class-variance-authority`, so every state is enumerated rather than conditional.
5. Every component that can be empty, loading or failed ships all three states in the same commit.
6. Typed props always; enums imported from `lib/generated/contract.ts`.
7. Tokens are added in pairs — light and dark, in the same commit — or they are not added.
