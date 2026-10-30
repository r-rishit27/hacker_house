"use client";

/**
 * Who the console is acting for: the signed-in person, not a choice on screen.
 *
 * The role comes from the server-signed session (`/v2/me`), the same token the
 * console API verifies on every call, so what a page shows and what the server
 * permits cannot drift apart. There is no switcher any more: to act as another
 * role, sign in as someone who holds it.
 *
 * Changing identity invalidates every query, because `may_execute` is computed
 * for a role and a cached answer for the previous one is a lie about what this
 * person can do.
 */

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { DEFAULT_ROLE, writeRole } from "@/lib/api";
import type { Role } from "@/lib/generated/contract";

export interface SessionUser {
  id: string;
  role: string;
}

interface RoleContext {
  role: Role;
  user: SessionUser | null;
  /** Kept for callers that used the old switcher; identity now comes only from sign-in. */
  setRole: (role: Role) => void;
}

const STAFF: readonly string[] = ["analyst", "team_lead", "fraud_manager"];

const Context = createContext<RoleContext>({ role: DEFAULT_ROLE, user: null, setRole: () => {} });

export function RoleProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const client = useQueryClient();

  useEffect(() => {
    let live = true;
    fetch("/v2/me", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => {
        if (!live || !me?.authenticated) return;
        setUser({ id: me.actor_id, role: me.actor_role });
        if (STAFF.includes(me.actor_role)) writeRole(me.actor_role as Role);
        void client.invalidateQueries();
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [client]);

  const role: Role = user && STAFF.includes(user.role) ? (user.role as Role) : DEFAULT_ROLE;
  const value = useMemo(() => ({ role, user, setRole: () => {} }), [role, user]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export const useRole = () => useContext(Context);

/** How each role reads in the interface. */
export const ROLE_LABELS: Record<string, string> = {
  analyst: "Analyst",
  team_lead: "Team lead",
  fraud_manager: "Fraud manager",
  regulator: "Regulator",
};
