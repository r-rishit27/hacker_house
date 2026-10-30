"use client";

/**
 * Sign in. One username and password for the whole product: the session it creates is a
 * server-signed token in an HttpOnly cookie, read by the console API and the v2 service alike,
 * and the role inside it decides where the person lands and what every page lets them do.
 */

import { Suspense, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";

const ROLES = [
  { role: "Analyst", can: "Work the alert queue, start investigations, read case files and their audit trail" },
  { role: "Team lead", can: "Everything an analyst can, and approve or reject L1 actions" },
  { role: "Fraud manager", can: "Approve L2 actions, the full audit trail, the change record, who has access" },
  { role: "Regulator", can: "Read-only: every investigation, its approval chain and the hash-chained audit trail" },
];

function LoginForm() {
  const params = useSearchParams();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/v2/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: f.get("username"), password: f.get("password") }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(r.status >= 500 && !body.detail
          ? "The sign-in service is not running. Start it with: cd backend && uvicorn api_v2.app:app --port 8100"
          : body.detail ?? `Sign-in failed (${r.status})`);
        return;
      }
      const home = body.role === "regulator" ? "/regulator" : "/";
      const next = params.get("next");
      // Only a path inside this app, never another site.
      const target = next && next.startsWith("/") && !next.startsWith("//") && body.role !== "regulator" ? next : home;
      window.location.replace(target);
    } catch {
      setError("The sign-in service is not reachable. Start it with: cd backend && uvicorn api_v2.app:app --port 8100");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4 rounded-lg border border-border bg-surface p-6 shadow-2">
      <div className="grid gap-1">
        <h1 className="text-h2">Sign in</h1>
        <p className="text-small text-fg-muted">Use the account your fraud manager gave you. Your role decides what you see and can do.</p>
      </div>
      <label className="grid gap-1 text-caption text-fg-muted">Username
        <input id="username" name="username" autoComplete="username" required autoFocus
          className="h-10 rounded-md border border-border-strong bg-surface px-3 text-body text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" />
      </label>
      <label className="grid gap-1 text-caption text-fg-muted">Password
        <input id="password" name="password" type="password" autoComplete="current-password" required
          className="h-10 rounded-md border border-border-strong bg-surface px-3 text-body text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" />
      </label>
      {error && <p role="alert" className="rounded-md bg-danger-tint px-3 py-2 text-small text-danger">{error}</p>}
      <button type="submit" disabled={busy}
        className="h-10 rounded-md bg-accent text-body-strong text-accent-contrast hover:opacity-90 disabled:opacity-60">
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="text-caption text-fg-subtle">Five wrong passwords lock the account for 15 minutes. Every sign-in and refusal is written to the audit trail.</p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-dvh place-items-center bg-bg py-10" style={{ paddingLeft: "max(16px, env(safe-area-inset-left, 0px))", paddingRight: "max(16px, env(safe-area-inset-right, 0px))" }}>
      <div className="grid w-full max-w-[920px] items-center gap-8 md:grid-cols-[1fr_400px]">
        <section className="grid gap-5">
          <div className="flex items-center gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element -- a fixed-size brand mark */}
            <img src="/logo.png" alt="" width={44} height={44} className="rounded-lg" />
            <div className="grid leading-tight">
              <span className="text-[18px] font-bold tracking-[0.14em]">SENTINEL</span>
              <span className="font-mono text-mono-sm text-fg-muted">agentic fraud investigator</span>
            </div>
          </div>
          <p className="max-w-[46ch] text-h3 font-normal text-fg-muted">
            Investigates card fraud on the bank&apos;s transaction graph, asks a person before it blocks anything,
            and records every step in an audit trail a regulator can check.
          </p>
          <ul className="grid gap-2">
            {ROLES.map((r) => (
              <li key={r.role} className="grid gap-0.5 rounded-md border border-border bg-surface px-3 py-2">
                <b className="text-small">{r.role}</b>
                <span className="text-caption text-fg-muted">{r.can}</span>
              </li>
            ))}
          </ul>
        </section>
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
