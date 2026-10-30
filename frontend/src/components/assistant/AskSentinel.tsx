"use client";

/**
 * Ask Sentinel: the floating assistant, on every page of the console.
 *
 * A question is answered by the v2 service (backend/api_v2/assistant.py) from
 * the investigations, audit trail, live graph, change record, project documents
 * and the person's own uploads — but only the parts the *signed-in* identity's
 * role may see. The role switcher in the masthead is a claim for the demo; this
 * panel signs in for real, and says whose permissions an answer was scoped to.
 *
 * Everything the panel shows about access is the server's: what it filtered,
 * which permission withheld matching material, which citations it removed.
 * Hiding something here would be decoration; the filter already happened.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent } from "react";
import { usePathname, useRouter } from "next/navigation";

import {
  assistantApi,
  V2_ROLE_LABELS,
  V2Error,
  type Answer,
  type ScopeItem,
  type SourceBody,
  type Upload,
  type V2Role,
  type Viewer,
} from "@/lib/assistant";
import { cn } from "@/lib/cn";

type Turn = { role: "user"; text: string } | { role: "assistant"; answer: Answer } | { role: "error"; text: string };

const SUGGEST: Record<V2Role, string[]> = {
  analyst: ["Why was HHG-014 called fraud? Cite the evidence.", "Which alerts are still uncertain, and why?",
    "Find closed cases similar to a device ring behind an anonymous proxy"],
  team_lead: ["What is waiting for L1 approval, and why?", "Summarise HHG-014 as a brief for the fraud manager",
    "What does card C13487-K1 look like in the graph?"],
  fraud_manager: ["Who approved the report on HHG-014, and who was refused?", "Any failed sign-ins or refused requests?",
    "What changed in the January data, and who approved it?"],
  regulator: ["Show the approval chain for HHG-014", "Which investigations filed a report, and on what grounds?",
    "How does Sentinel make sure a person approves every block?"],
};

const SUPPORT: Record<Answer["support"], { label: string; cls: string }> = {
  supported: { label: "Supported by sources", cls: "bg-success-tint text-success" },
  partial: { label: "Partly supported", cls: "bg-warning-tint text-warning" },
  unsupported: { label: "Unverified", cls: "bg-warning-tint text-warning" },
  not_found: { label: "Not found in what you can see", cls: "bg-surface-3 text-fg-muted" },
  refused: { label: "Outside what I can do", cls: "bg-surface-3 text-fg-muted" },
};

const storeKey = (who: string) => `askSentinel:${who}`;

function readTurns(who: string): Turn[] {
  try {
    const v = JSON.parse(window.sessionStorage.getItem(storeKey(who)) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function writeTurns(who: string, turns: Turn[]) {
  try {
    window.sessionStorage.setItem(storeKey(who), JSON.stringify(turns.slice(-20)));
  } catch {
    /* no storage: the conversation lasts as long as the page */
  }
}

function forgetAll() {
  try {
    Object.keys(window.sessionStorage).filter((k) => k.startsWith("askSentinel:")).forEach((k) => window.sessionStorage.removeItem(k));
  } catch {
    /* nothing stored */
  }
}

// ── a deliberately small Markdown renderer: escape everything, then allow a few safe forms back ──

const esc = (v: string) =>
  v.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

function inline(t: string) {
  return t
    .replace(/`([^`]+)`/g, '<code class="rounded-sm bg-surface-3 px-1 font-mono text-mono-sm">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[S(\d{1,2})\]/g, (_m, n) =>
      `<button type="button" data-cite="${n}" class="mx-px rounded-sm bg-info-tint px-1 align-[2px] font-mono text-[10px] font-semibold text-info" aria-label="Source ${n}">S${n}</button>`);
}

function renderMarkdown(src: string): string {
  const lines = esc(src).split("\n");
  const out: string[] = [];
  const at = (k: number) => lines[k] ?? "";
  let list: "ul" | "ol" | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) out.push(`<p class="mb-2 last:mb-0">${inline(para.join("<br>"))}</p>`);
    para = [];
  };
  const flush = () => {
    flushPara();
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (let i = 0; i < lines.length; ) {
    const l = at(i);
    if (/^```/.test(l)) {
      flush();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(at(i))) buf.push(at(i++));
      out.push(`<pre class="mb-2 overflow-x-auto rounded-md bg-surface-3 p-2 font-mono text-mono-sm">${buf.join("\n")}</pre>`);
      i++;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(at(i + 1))) {
      flush();
      const cells = (r: string) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = cells(l);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(at(i))) rows.push(cells(at(i++)));
      const td = "border border-border px-1.5 py-1 text-left align-top";
      out.push(`<div class="mb-2 overflow-x-auto"><table class="border-collapse text-caption"><thead><tr>${head.map((c) => `<th class="${td} bg-surface-2">${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td class="${td}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      flush();
      out.push(`<h4 class="mb-1 mt-2 text-body-strong">${inline(h[2] ?? "")}</h4>`);
      i++;
      continue;
    }
    const li = l.match(/^\s*([-*]|\d+\.)\s+(.*)/);
    if (li) {
      flushPara();
      const t = /\d/.test(li[1] ?? "") ? "ol" : "ul";
      if (list !== t) {
        if (list) out.push(`</${list}>`);
        out.push(`<${t} class="mb-2 ${t === "ol" ? "list-decimal" : "list-disc"} space-y-0.5 pl-5">`);
        list = t;
      }
      out.push(`<li>${inline(li[2] ?? "")}</li>`);
      i++;
      continue;
    }
    if (!l.trim()) {
      flush();
      i++;
      continue;
    }
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
    para.push(l);
    i++;
  }
  flush();
  return out.join("");
}

// ── icons, on the console's 24-unit grid ──

const PATHS = {
  chat: "M4 6.5A3 3 0 0 1 7 3.5h10a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-6l-5 4v-4H7a3 3 0 0 1-3-3zM8.5 8.5h7M8.5 11.5h4.5",
  close: "M6 6l12 12M18 6L6 18",
  send: "M4 12L20 4l-4.5 16-3.5-6.5zM12 13.5L20 4",
  clip: "M17.5 11.5l-6.8 6.8a4.1 4.1 0 0 1-5.8-5.8l7.4-7.4a2.8 2.8 0 0 1 3.9 3.9l-7.2 7.2a1.4 1.4 0 0 1-2-2l6.6-6.6",
  shield: "M12 3l7 2.6v5.2c0 4.5-3 7.1-7 8.7-4-1.6-7-4.2-7-8.7V5.6zM9 12l2.2 2.2 4-4.3",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  doc: "M6.5 3h7.5l4 4v14h-11.5zM14 3v4h4",
  save: "M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19h14",
  copy: "M8.5 8.5h10v12h-10zM5.5 15.5v-10a1.5 1.5 0 0 1 1.5-1.5h8.5",
  plus: "M12 5v14M5 12h14",
};

function Glyph({ name, size = 18, className }: { name: keyof typeof PATHS; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden className={cn("shrink-0", className)}>
      <path d={PATHS[name]} />
    </svg>
  );
}

// ── the panel ──

export function AskSentinel() {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<"checking" | "signin" | "ready" | "offline">("checking");
  const [offlineWhy, setOfflineWhy] = useState("");
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [scope, setScope] = useState<ScopeItem[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [viewing, setViewing] = useState<SourceBody | null>(null);
  const [signinError, setSigninError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    setPhase("checking");
    try {
      const s = await assistantApi.scope();
      if (!s.viewer.authenticated) {
        setOfflineWhy("The v2 service has no signing secret, so it cannot tell who you are. Set SENTINEL_AUTH_SECRET in .env.");
        setPhase("offline");
        return;
      }
      setViewer(s.viewer);
      setScope(s.scope);
      setUploads(s.uploads);
      setTurns(readTurns(s.viewer.actor_id));
      setPhase("ready");
    } catch (e) {
      if (e instanceof V2Error && e.status === 401) setPhase("signin");
      else {
        setOfflineWhy(e instanceof Error ? e.message : String(e));
        setPhase("offline");
      }
    }
  }, []);

  useEffect(() => {
    if (open && phase === "checking") void load();
  }, [open, phase, load]);

  useEffect(() => {
    if (viewer) writeTurns(viewer.actor_id, turns);
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, viewer, busy]);

  useEffect(() => {
    if (open && phase === "ready") inputRef.current?.focus();
  }, [open, phase]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape" || !open) return;
      if (viewing) setViewing(null);
      else setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, viewing]);

  async function signIn(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setSigninError("");
    try {
      await assistantApi.login(String(f.get("username") ?? ""), String(f.get("password") ?? ""));
      await load();
    } catch (err) {
      setSigninError(err instanceof Error ? err.message : String(err));
    }
  }

  async function signOut() {
    forgetAll();
    await assistantApi.logout().catch(() => undefined);
    // One session for the whole product: signing out here signs out everywhere.
    window.location.assign("/login");
  }

  async function ask(text?: string) {
    const q = (text ?? question).trim();
    if (!q || busy) return;
    setQuestion("");
    type Said = { role: "user" | "assistant"; text: string };
    const history = turns.flatMap((t): Said[] =>
      t.role === "user" ? [{ role: "user", text: t.text }]
        : t.role === "assistant" ? [{ role: "assistant", text: t.answer.answer }] : []);
    setTurns((ts) => [...ts, { role: "user", text: q }]);
    setBusy(true);
    try {
      const answer = await assistantApi.ask(q, history);
      setTurns((ts) => [...ts, { role: "assistant", answer }]);
    } catch (e) {
      if (e instanceof V2Error && e.status === 401) setPhase("signin");
      setTurns((ts) => [...ts, { role: "error", text: e instanceof Error ? e.message : String(e) }]);
    } finally {
      setBusy(false);
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 400_000) {
      setTurns((ts) => [...ts, { role: "error", text: `${file.name} is ${(file.size / 1000).toFixed(0)} KB; the limit is 400 KB of text.` }]);
      return;
    }
    try {
      const meta = await assistantApi.upload(file.name, await file.text());
      setUploads((u) => [...u, meta]);
      const warn = meta.instruction_like_spans.length
        ? `\n\nIt contains wording aimed at an automated system (“${meta.instruction_like_spans[0]}”). I will read that as data, not as instructions.`
        : "";
      setTurns((ts) => [...ts, {
        role: "assistant",
        answer: {
          answer: `Added **${meta.name}** (${meta.chars.toLocaleString()} characters). I will read it alongside everything your role can see. Only you can see it.${warn}`,
          support: "supported", sources: [], withheld: [], notes: [], model: null, tokens: 0, latency_s: 0,
          guardrails: { sources_searched: 0, sources_withheld_by_role: 0 },
        },
      }]);
    } catch (e) {
      setTurns((ts) => [...ts, { role: "error", text: e instanceof Error ? e.message : String(e) }]);
    }
  }

  async function removeUpload(id: string) {
    try {
      await assistantApi.remove(id);
      setUploads((u) => u.filter((x) => x.doc_id !== id));
    } catch (e) {
      setTurns((ts) => [...ts, { role: "error", text: e instanceof Error ? e.message : String(e) }]);
    }
  }

  async function openSource(a: Answer, marker: string) {
    const s = a.sources.find((x) => x.marker === marker);
    if (!s) return;
    const caseLink = s.link.match(/^\/(?:portal|regulator)#([A-Z]{2,4}-\d{3,4})$/);
    if (caseLink) {
      router.push(`/cases/${caseLink[1]}`);
      return;
    }
    try {
      setViewing(await assistantApi.source(s.id));
    } catch {
      // Live graph facts are read when asked and not stored; show what the answer carried.
      setViewing({ id: s.id, kind: `${s.kind} · read live from the graph when you asked`, title: s.title, text: `${s.snippet}…`, link: "" });
    }
  }

  function onAnswerClick(e: MouseEvent<HTMLDivElement>, a: Answer) {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-cite]");
    if (b) void openSource(a, `S${b.dataset.cite}`);
  }

  function saveBrief(a: Answer, q: string) {
    const who = viewer ? `${viewer.actor_id} (${V2_ROLE_LABELS[viewer.actor_role] ?? viewer.actor_role})` : "";
    const doc = `# ${q}\n\n_Ask Sentinel · ${who} · ${new Date().toISOString().slice(0, 16).replace("T", " ")} · ${SUPPORT[a.support]?.label ?? a.support}_\n\n${a.answer}\n\n## Sources\n\n`
      + a.sources.map((s) => `- **${s.marker}** ${s.title} (${s.kind})`).join("\n")
      + (a.withheld.length ? `\n\n_Not included, outside this role: ${a.withheld.map((w) => w.what).join("; ")}._` : "") + "\n";
    const url = URL.createObjectURL(new Blob([doc], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `sentinel-brief-${new Date().toISOString().slice(0, 10)}.md`;
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      link.remove();
    }, 500);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void ask();
    }
  }

  const granted = scope.filter((s) => s.granted).length;
  if (pathname === "/login") return null; // nothing to ask before signing in

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-controls="ask-sentinel"
          aria-expanded={false}
          className="fixed z-dialog inline-flex items-center gap-2 rounded-full bg-accent py-2 pl-2 pr-4 text-body-strong text-accent-contrast shadow-3 transition-opacity duration-fast hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          style={{ right: "calc(20px + env(safe-area-inset-right, 0px))", bottom: "calc(20px + env(safe-area-inset-bottom, 0px))" }}
        >
          <img src="/logo.png" alt="" width={26} height={26} className="shrink-0 rounded-full ring-2 ring-accent-contrast" />
          Ask Sentinel
        </button>
      )}

      {open && (
        <section
          id="ask-sentinel"
          aria-label="Ask Sentinel"
          className="fixed z-dialog grid grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden rounded-lg border border-border-strong bg-surface shadow-3"
          style={{
            right: "calc(16px + env(safe-area-inset-right, 0px))",
            bottom: "calc(16px + env(safe-area-inset-bottom, 0px))",
            width: "min(460px, calc(100vw - 32px))",
            height: "min(700px, calc(100dvh - 80px))",
          }}
        >
          <header className="flex items-center gap-2.5 border-b border-border bg-surface-2 px-3 py-2.5">
            <img src="/logo.png" alt="" width={28} height={28} className="shrink-0 rounded-md" />
            <div className="grid min-w-0 leading-tight">
              <b className="text-body-strong">Ask Sentinel</b>
              <span className="truncate text-caption text-fg-muted">
                {viewer ? <>Signed in as <span className="font-mono">{viewer.actor_id}</span> · {V2_ROLE_LABELS[viewer.actor_role] ?? viewer.actor_role}</> : "Answers from what your role can see, with sources"}
              </span>
            </div>
            <div className="ml-auto flex items-center gap-1">
              {phase === "ready" && (
                <>
                  <button type="button" onClick={() => setTurns([])} title="New conversation" aria-label="New conversation"
                    className="rounded-md p-1.5 text-fg-muted hover:bg-surface-3 hover:text-fg"><Glyph name="plus" size={16} /></button>
                  <button type="button" onClick={signOut} className="rounded-md px-2 py-1 text-caption text-fg-muted hover:bg-surface-3 hover:text-fg">Sign out</button>
                </>
              )}
              <button type="button" onClick={() => setOpen(false)} aria-label="Close"
                className="rounded-md p-1.5 text-fg-muted hover:bg-surface-3 hover:text-fg"><Glyph name="close" size={16} /></button>
            </div>
          </header>

          {phase === "ready" ? (
            <details className="border-b border-border text-caption">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-fg-muted [&::-webkit-details-marker]:hidden">
                <Glyph name="shield" size={14} className="text-success" />
                I can search {granted} of {scope.length} kinds of record for your role
                <span className="ml-auto text-fg-subtle">details</span>
              </summary>
              <ul className="grid gap-1 px-3 pb-2.5">
                {scope.map((s) => (
                  <li key={s.what + s.permission} className="flex items-baseline gap-2">
                    <span className={s.granted ? "font-bold text-success" : "text-fg-subtle"}>{s.granted ? "✓" : "–"}</span>
                    <span className={s.granted ? "text-fg" : "text-fg-subtle"}>
                      {s.what}{!s.granted && <span className="font-mono text-mono-sm"> (needs {s.permission})</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : <div />}

          <div ref={logRef} className="grid content-start gap-3 overflow-y-auto bg-bg p-3" aria-live="polite">
            {phase === "checking" && <p className="text-small text-fg-muted">Connecting to Sentinel…</p>}

            {phase === "offline" && (
              <div className="grid gap-2 rounded-md border border-danger bg-danger-tint p-3 text-small text-danger">
                <b>Ask Sentinel is not available</b>
                <span className="break-words">{offlineWhy}</span>
                <button type="button" onClick={() => void load()} className="justify-self-start rounded-md border border-danger px-2 py-1 text-caption">Try again</button>
              </div>
            )}

            {phase === "signin" && (
              <form onSubmit={signIn} className="grid gap-3 rounded-lg border border-border bg-surface p-4">
                <div className="grid gap-1">
                  <b className="text-h3">Sign in to ask</b>
                  <p className="text-small text-fg-muted">
                    Answers draw only on what your own role may see, so the assistant needs your real sign-in, not the role switcher.
                  </p>
                </div>
                <label className="grid gap-1 text-caption text-fg-muted">Username
                  <input id="ask-username" name="username" autoComplete="username" required
                    className="h-9 rounded-md border border-border-strong bg-surface px-2.5 text-body text-fg" />
                </label>
                <label className="grid gap-1 text-caption text-fg-muted">Password
                  <input id="ask-password" name="password" type="password" autoComplete="current-password" required
                    className="h-9 rounded-md border border-border-strong bg-surface px-2.5 text-body text-fg" />
                </label>
                {signinError && <p className="rounded-md bg-danger-tint px-2.5 py-1.5 text-small text-danger">{signinError}</p>}
                <button type="submit" className="h-9 rounded-md bg-accent text-body-strong text-accent-contrast hover:opacity-90">Sign in</button>
                <p className="text-caption text-fg-subtle">Five wrong passwords lock the account for 15 minutes. Every sign-in is recorded.</p>
              </form>
            )}

            {phase === "ready" && turns.length === 0 && viewer && (
              <>
                <div className="rounded-lg border border-border bg-surface p-3 text-small">
                  <p className="mb-2">
                    Hello {viewer.actor_id}. I answer from the investigations, audit trail, live graph and documents your role
                    (<b>{V2_ROLE_LABELS[viewer.actor_role] ?? viewer.actor_role}</b>) can see, and every answer links to its sources.
                  </p>
                  <p className="text-fg-muted">Add your own document with the paper-clip and I will read it too. Only you can see it.</p>
                </div>
                <div className="grid gap-1.5">
                  {(SUGGEST[viewer.actor_role] ?? SUGGEST.analyst).map((q) => (
                    <button key={q} type="button" onClick={() => void ask(q)}
                      className="rounded-md border border-dashed border-border-strong bg-surface px-2.5 py-2 text-left text-small hover:border-solid hover:border-accent">
                      {q}
                    </button>
                  ))}
                </div>
              </>
            )}

            {phase === "ready" && turns.map((t, i) => {
              if (t.role === "user") {
                return (
                  <div key={i} className="max-w-[86%] justify-self-end whitespace-pre-wrap break-words rounded-[12px_12px_3px_12px] bg-fg px-3 py-2 text-small text-surface">
                    {t.text}
                  </div>
                );
              }
              if (t.role === "error") {
                return <div key={i} className="break-words rounded-md border border-danger bg-danger-tint px-3 py-2 text-small text-danger">{t.text}</div>;
              }
              const a = t.answer;
              const prev = turns[i - 1];
              const q = prev && prev.role === "user" ? prev.text : "Ask Sentinel answer";
              const g = a.guardrails;
              const injected = (g.instruction_like_text_in_question?.length ?? 0) + (g.instruction_like_text_in_sources?.length ?? 0) > 0;
              return (
                <div key={i} className="grid min-w-0 gap-1.5">
                  <span className={cn("justify-self-start rounded-full px-2 py-px text-caption", SUPPORT[a.support]?.cls)}>{SUPPORT[a.support]?.label ?? a.support}</span>
                  <div className="min-w-0 break-words rounded-[3px_12px_12px_12px] border border-border bg-surface px-3 py-2.5 text-small"
                    onClick={(e) => onAnswerClick(e, a)} dangerouslySetInnerHTML={{ __html: renderMarkdown(a.answer) }} />
                  {a.withheld.length > 0 && (
                    <div className="flex gap-1.5 rounded-md bg-warning-tint px-2.5 py-1.5 text-caption text-warning">
                      <Glyph name="lock" size={14} />
                      <span>Some matching material is outside your role: {a.withheld.map((w) => `${w.what} (${w.permission})`).join("; ")}.</span>
                    </div>
                  )}
                  {a.sources.length > 0 && (
                    <div className="grid gap-1">
                      <span className="text-overline uppercase text-fg-subtle">Sources</span>
                      {a.sources.map((s) => (
                        <button key={s.marker} type="button" onClick={() => void openSource(a, s.marker)}
                          className={cn("grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2 rounded-md border bg-surface px-2 py-1.5 text-left text-caption hover:border-accent",
                            s.cited ? "border-info" : "border-border")}>
                          <span className="font-mono text-mono-sm font-semibold text-info">{s.marker}</span>
                          <span className="break-words font-semibold text-fg">{s.title}</span>
                          <span className="col-start-2 text-fg-subtle">{s.kind}{s.cited ? " · cited" : ""} · {s.found_by.join(" + ")}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {a.model !== null || a.sources.length > 0 ? (
                    <div className="flex items-start gap-1.5 text-caption text-fg-muted">
                      <Glyph name="shield" size={13} className="mt-px text-success" />
                      <span>
                        Searched {g.sources_searched} sources your role can see; {g.sources_withheld_by_role} withheld by role
                        {g.citations_removed ? ` · ${g.citations_removed} invalid citation(s) removed` : ""}
                        {injected ? " · instruction-like text treated as data" : ""}
                        {a.notes.length ? ` · ${a.notes.join("; ")}` : ""} · {a.model ?? "no model"} · {a.tokens.toLocaleString()} tokens · {a.latency_s} s
                      </span>
                    </div>
                  ) : null}
                  {a.sources.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      <button type="button" onClick={() => void navigator.clipboard?.writeText(a.answer).catch(() => undefined)}
                        className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-caption text-fg-muted hover:border-accent hover:text-accent">
                        <Glyph name="copy" size={13} />Copy</button>
                      <button type="button" onClick={() => saveBrief(a, q)}
                        className="inline-flex items-center gap-1 rounded-md border border-border bg-surface px-2 py-0.5 text-caption text-fg-muted hover:border-accent hover:text-accent">
                        <Glyph name="save" size={13} />Save as brief (.md)</button>
                    </div>
                  )}
                </div>
              );
            })}

            {busy && (
              <div className="inline-flex gap-1 justify-self-start rounded-[3px_12px_12px_12px] border border-border bg-surface px-3 py-3" aria-label="Searching">
                {[0, 1, 2].map((n) => (
                  <i key={n} className="size-1.5 rounded-full bg-fg-subtle motion-safe:animate-pulse" style={{ animationDelay: `${n * 150}ms` }} />
                ))}
              </div>
            )}
          </div>

          <footer className="grid gap-1.5 border-t border-border bg-surface p-2">
            {phase === "ready" ? (
              <>
                {uploads.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {uploads.map((u) => (
                      <span key={u.doc_id} title={`${u.name} · ${u.chars.toLocaleString()} characters`}
                        className="inline-flex max-w-full items-center gap-1 rounded-full bg-info-tint py-0.5 pl-2 pr-1 text-caption text-info">
                        <Glyph name="doc" size={12} />
                        <span className="max-w-[180px] truncate">{u.name}</span>
                        <button type="button" onClick={() => void removeUpload(u.doc_id)} aria-label={`Remove ${u.name}`} className="px-1 font-bold">×</button>
                      </span>
                    ))}
                  </div>
                )}
                <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-end gap-1.5">
                  <button type="button" onClick={() => fileRef.current?.click()} title="Add a document (.txt, .md, .csv, .json, .log)" aria-label="Add a document"
                    className="grid size-10 place-items-center rounded-md border border-border-strong bg-surface text-fg-muted hover:border-accent hover:text-accent">
                    <Glyph name="clip" />
                  </button>
                  <textarea ref={inputRef} id="ask-question" rows={1} value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={onKeyDown}
                    placeholder="Ask about an alert, a card, an approval, a policy…" aria-label="Your question"
                    className="max-h-32 min-h-10 w-full resize-none rounded-md border border-border-strong bg-surface px-2.5 py-2 text-body text-fg" />
                  <button type="button" onClick={() => void ask()} disabled={busy || !question.trim()} aria-label="Send"
                    className="grid size-10 place-items-center rounded-md bg-accent text-accent-contrast hover:opacity-90 disabled:opacity-50">
                    <Glyph name="send" />
                  </button>
                </div>
                <input ref={fileRef} type="file" hidden accept=".txt,.md,.csv,.json,.log,.tsv,text/*"
                  onChange={(e) => { void onFile(e.target.files?.[0]); e.target.value = ""; }} />
                <p className="px-0.5 text-caption text-fg-subtle">Read-only: it explains and drafts, never blocks, approves or files. Enter sends · Shift+Enter for a new line.</p>
              </>
            ) : (
              <p className="px-0.5 text-caption text-fg-subtle">Each answer is scoped to your role and recorded in the audit trail.</p>
            )}
          </footer>

          {viewing && (
            <div className="absolute inset-0 grid grid-rows-[auto_minmax(0,1fr)] bg-surface">
              <header className="flex items-center gap-2.5 border-b border-border bg-surface-2 px-3 py-2.5">
                <span className="grid size-7 place-items-center rounded-md bg-info text-info-contrast"><Glyph name="doc" size={16} /></span>
                <div className="grid min-w-0 leading-tight">
                  <b className="truncate text-body-strong">{viewing.title}</b>
                  <span className="truncate text-caption text-fg-muted">{viewing.kind}</span>
                </div>
                <button type="button" onClick={() => setViewing(null)} aria-label="Back to the conversation"
                  className="ml-auto rounded-md p-1.5 text-fg-muted hover:bg-surface-3 hover:text-fg"><Glyph name="close" size={16} /></button>
              </header>
              <pre className="m-0 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-mono-sm">{viewing.text}</pre>
            </div>
          )}
        </section>
      )}
    </>
  );
}
