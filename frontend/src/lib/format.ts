/**
 * Display rules from DESIGN_SYSTEM.md §14. Amounts always carry the currency and
 * two decimals; probabilities always carry two decimals. A bare number in the UI
 * is a bug.
 */

export const usd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });

export const probability = (p: number) => p.toFixed(2);

export const lr = (value: number) => value.toFixed(2);

/** Ids, timestamps and refs are rendered in mono; this only trims for display. */
export const shortRef = (ref: string, max = 56) =>
  ref.length <= max ? ref : `${ref.slice(0, max - 1)}…`;

/** DeviceProfile labels are "DeviceInfo | OS | browser | screen". */
export const deviceParts = (label: string) => label.split("|").map((s) => s.trim());
