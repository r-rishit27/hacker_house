"use client";

/**
 * Who is signed in, and the way out.
 *
 * This used to be a role switcher. Identity now comes from sign-in, so the
 * masthead states it instead of offering to change it: the role badge is what
 * the server will enforce on every call, and Sign out ends the session for the
 * console API, the regulator view and Ask Sentinel at once.
 */

import { ROLE_LABELS, useRole } from "@/lib/role";

const ROLE_TONE: Record<string, string> = {
  analyst: "text-fg-muted border-border-strong",
  team_lead: "text-warning border-warning",
  fraud_manager: "text-danger border-danger",
  regulator: "text-info border-info",
};

export function RoleSwitcher() {
  const { user } = useRole();

  async function signOut() {
    try {
      Object.keys(window.sessionStorage).filter((k) => k.startsWith("askSentinel:")).forEach((k) => window.sessionStorage.removeItem(k));
    } catch {
      /* nothing stored */
    }
    await fetch("/v2/logout", { method: "POST", credentials: "same-origin" }).catch(() => undefined);
    window.location.assign("/login");
  }

  if (!user) return <span className="text-caption text-fg-subtle">Checking sign-in…</span>;
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-mono-sm text-fg">{user.id}</span>
      <span className={`rounded-full border px-2 py-px text-caption font-semibold ${ROLE_TONE[user.role] ?? ROLE_TONE.analyst}`}>
        {ROLE_LABELS[user.role] ?? user.role}
      </span>
      <button type="button" onClick={signOut}
        className="h-8 rounded-md border border-border-strong bg-surface px-2.5 text-small text-fg hover:border-accent hover:text-accent">
        Sign out
      </button>
    </div>
  );
}
