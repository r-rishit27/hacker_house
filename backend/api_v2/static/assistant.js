// Ask Sentinel — the floating assistant on every signed-in page (server side: api_v2/assistant.py).
// It answers only from what the signed-in role may see; the server filters, this only displays.
// Every answer lists its sources as reference links, says what the role could not see, and can be
// saved as a Markdown brief.
(function () {
  if (window.__askSentinel) return; window.__askSentinel = true;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ROLE_NAME = { analyst: "Analyst", team_lead: "Team lead", fraud_manager: "Fraud manager", regulator: "Regulator" };
  const SUGGEST = {
    analyst: ["Why was HHG-014 called fraud? Cite the evidence.", "Which alerts are still uncertain, and why?", "Find closed cases similar to a device ring behind an anonymous proxy"],
    team_lead: ["What is waiting for my L1 approval?", "Summarise HHG-014 as a brief for the fraud manager", "Which cases did the model decide, and what did it score?"],
    fraud_manager: ["Who approved the report on HHG-014, and who was refused?", "Any failed sign-ins or refused requests today?", "What changed in the January data, and who approved it?"],
    regulator: ["Show the approval chain for HHG-014", "Which investigations filed a report, and on what grounds?", "How does Sentinel make sure a person approves every block?"],
  };
  const I = {
    chat: '<path d="M3 4.5A2.5 2.5 0 0 1 5.5 2h9A2.5 2.5 0 0 1 17 4.5v6a2.5 2.5 0 0 1-2.5 2.5H9l-4 3.5V13h.5A2.5 2.5 0 0 1 3 10.5z"/><path d="M7 6.8h6M7 9.3h4"/>',
    close: '<path d="M5 5l10 10M15 5L5 15"/>',
    send: '<path d="M3.5 10L16.5 3.5 12.5 16.5 9.6 11 3.5 10z"/><path d="M9.6 11l6.9-7.5"/>',
    clip: '<path d="M14.5 9.5l-5.6 5.6a3.4 3.4 0 0 1-4.8-4.8l6.2-6.2a2.3 2.3 0 0 1 3.2 3.2l-6 6a1.1 1.1 0 0 1-1.6-1.6l5.5-5.5"/>',
    shield: '<path d="M10 2.5l6 2.2v4.4c0 3.8-2.6 6-6 7.4-3.4-1.4-6-3.6-6-7.4V4.7z"/><path d="M7.4 10l1.8 1.8 3.4-3.6"/>',
    doc: '<path d="M5 2.5h6.5L15 6v11.5H5z"/><path d="M11.5 2.5V6H15"/>',
    save: '<path d="M10 3v9M6.5 8.5L10 12l3.5-3.5M4 15.5h12"/>',
    copy: '<rect x="6.5" y="6.5" width="9" height="10" rx="1.5"/><path d="M4.5 13V4.5A1.5 1.5 0 0 1 6 3h7"/>',
    lock: '<rect x="4.5" y="9" width="11" height="8" rx="1.5"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
  };
  const ico = (n, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${I[n]}</svg>`;

  const css = `
  .as-fab { position: fixed; right: calc(20px + env(safe-area-inset-right, 0px)); bottom: calc(20px + env(safe-area-inset-bottom, 0px)); z-index: 60;
    display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px 8px 8px; border-radius: 999px; border: 0;
    background: var(--accent); color: var(--accent-contrast, #fff); font-weight: 700; box-shadow: 0 6px 20px rgb(60 42 26 / .25) }
  .as-fab:hover { filter: brightness(1.08) } .as-fab[aria-expanded="true"] { display: none }
  .as-panel { position: fixed; right: calc(16px + env(safe-area-inset-right, 0px)); bottom: calc(16px + env(safe-area-inset-bottom, 0px)); z-index: 61;
    width: min(460px, calc(100vw - 32px - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px)));
    height: min(680px, calc(100vh - 90px)); display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto;
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius-lg, 10px); box-shadow: 0 16px 48px rgb(60 42 26 / .22); overflow: hidden }
  .as-panel[hidden] { display: none !important }
  .as-head { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--border); background: var(--surface-2) }
  .as-head .t { display: grid; line-height: 1.2; min-width: 0 } .as-head b { font-size: .98rem } .as-head small { color: var(--fg-muted); font-size: .74rem }
  .as-mark { width: 28px; height: 28px; border-radius: 8px; background: var(--accent-bold); color: #fff; display: grid; place-items: center; flex: none }
  .as-logo { width: 26px; height: 26px; border-radius: 7px; background: var(--accent-bold) var(--logo, none) center / cover no-repeat; flex: none; display: inline-block }
  .as-fab .as-logo { border-radius: 50%; box-shadow: 0 0 0 2px var(--accent-contrast, #fff) }
  .as-x { margin-left: auto; border: 0; background: transparent; padding: 6px; border-radius: 6px; color: var(--fg-muted) } .as-x:hover { background: var(--surface-3); color: var(--fg) }
  .as-scope { border-bottom: 1px solid var(--border); font-size: .8rem }
  .as-scope summary { padding: 7px 12px; cursor: pointer; color: var(--fg-muted); display: flex; gap: 6px; align-items: center; list-style: none }
  .as-scope summary::-webkit-details-marker { display: none }
  .as-scope ul { margin: 0; padding: 0 12px 10px; list-style: none; display: grid; gap: 3px }
  .as-scope li { display: flex; gap: 6px; align-items: baseline } .as-scope .on { color: var(--success); font-weight: 700 } .as-scope .off { color: var(--fg-subtle) }
  .as-log { overflow-y: auto; padding: 12px; display: grid; gap: 12px; align-content: start; background: var(--bg) }
  .as-msg { display: grid; gap: 6px; max-width: 100%; min-width: 0 }
  .as-msg.user { justify-self: end; max-width: 86% } .as-msg.user .b { background: var(--fg); color: var(--surface); border-radius: 12px 12px 3px 12px; padding: 8px 11px; white-space: pre-wrap; overflow-wrap: anywhere }
  .as-msg.bot .b { background: var(--surface); border: 1px solid var(--border); border-radius: 3px 12px 12px 12px; padding: 10px 12px; overflow-wrap: anywhere; min-width: 0 }
  .as-msg .b p { margin: 0 0 8px } .as-msg .b p:last-child { margin: 0 } .as-msg .b h3, .as-msg .b h4 { margin: 8px 0 4px; font-size: .92rem }
  .as-msg .b ul, .as-msg .b ol { margin: 0 0 8px; padding-left: 18px } .as-msg .b code { font: .82em var(--font-mono); background: var(--surface-3); padding: 0 4px; border-radius: 3px }
  .as-msg .b pre { background: var(--surface-3); padding: 8px; border-radius: 6px; overflow-x: auto; font: .78rem var(--font-mono) }
  .as-msg .b .tw { overflow-x: auto; margin: 0 0 8px } .as-msg .b table { border-collapse: collapse; font-size: .8rem } .as-msg .b th, .as-msg .b td { border: 1px solid var(--border); padding: 3px 6px; text-align: left }
  .as-cite { display: inline-block; font: 600 .66rem var(--font-mono); background: var(--info-tint); color: var(--info); border: 0; border-radius: 4px; padding: 0 4px; margin: 0 1px; vertical-align: 2px; cursor: pointer }
  .as-sup { font-size: .72rem; font-weight: 600; padding: 1px 7px; border-radius: 999px; justify-self: start }
  .as-sup.supported { background: var(--success-tint); color: var(--success) } .as-sup.partial, .as-sup.unsupported { background: var(--warning-tint); color: var(--warning) }
  .as-sup.not_found, .as-sup.refused { background: var(--surface-3); color: var(--fg-muted) }
  .as-refs { display: grid; gap: 4px } .as-refs .h { font-size: .68rem; letter-spacing: .1em; text-transform: uppercase; color: var(--fg-subtle) }
  .as-ref { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px 8px; align-items: baseline; padding: 6px 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); font-size: .8rem; text-align: left; width: 100%; cursor: pointer; color: var(--fg) }
  .as-ref:hover { border-color: var(--accent) } .as-ref.cited { border-color: var(--info) }
  .as-ref .m { font: 600 .7rem var(--font-mono); color: var(--info) } .as-ref .tt { font-weight: 600; overflow-wrap: anywhere }
  .as-ref .k { grid-column: 2; color: var(--fg-subtle); font-size: .72rem }
  .as-guard { display: flex; gap: 6px; align-items: flex-start; font-size: .74rem; color: var(--fg-muted) } .as-guard svg { flex: none; color: var(--success); margin-top: 1px }
  .as-withheld { font-size: .78rem; padding: 6px 8px; border-radius: 7px; background: var(--warning-tint); color: var(--warning); display: flex; gap: 6px }
  .as-tools { display: flex; gap: 6px; flex-wrap: wrap } .as-tools button { border: 1px solid var(--border); background: var(--surface); border-radius: 6px; padding: 3px 8px; font-size: .74rem; display: inline-flex; gap: 4px; align-items: center; color: var(--fg-muted) }
  .as-tools button:hover { border-color: var(--accent); color: var(--accent) }
  .as-sugg { display: grid; gap: 6px } .as-sugg button { text-align: left; border: 1px dashed var(--border-strong); background: var(--surface); border-radius: 8px; padding: 7px 10px; font-size: .82rem; color: var(--fg) }
  .as-sugg button:hover { border-style: solid; border-color: var(--accent) }
  .as-foot { border-top: 1px solid var(--border); padding: 8px; display: grid; gap: 6px; background: var(--surface) }
  .as-docs { display: flex; gap: 4px; flex-wrap: wrap } .as-docs span { display: inline-flex; gap: 4px; align-items: center; font-size: .72rem; padding: 2px 4px 2px 8px; border-radius: 999px; background: var(--info-tint); color: var(--info); max-width: 100% }
  .as-docs span em { font-style: normal; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px } .as-docs button { border: 0; background: transparent; color: inherit; padding: 0 3px; font-weight: 700 }
  .as-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 6px; align-items: end }
  .as-row textarea { resize: none; min-height: 40px; max-height: 140px; padding: 9px 10px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface); width: 100% }
  .as-ib { border: 1px solid var(--border-strong); background: var(--surface); border-radius: 8px; width: 40px; height: 40px; display: grid; place-items: center; color: var(--fg-muted) }
  .as-ib:hover { border-color: var(--accent); color: var(--accent) } .as-ib.go { background: var(--accent); border-color: var(--accent); color: #fff } .as-ib:disabled { opacity: .5; cursor: not-allowed }
  .as-hint { font-size: .7rem; color: var(--fg-subtle); padding: 0 2px }
  .as-typing { display: inline-flex; gap: 4px; padding: 10px 12px } .as-typing i { width: 6px; height: 6px; border-radius: 50%; background: var(--fg-subtle); animation: as-b 1s infinite ease-in-out }
  .as-typing i:nth-child(2) { animation-delay: .15s } .as-typing i:nth-child(3) { animation-delay: .3s }
  @keyframes as-b { 0%, 80%, 100% { opacity: .3 } 40% { opacity: 1 } }
  .as-view { position: absolute; inset: 0; background: var(--surface); display: grid; grid-template-rows: auto minmax(0, 1fr); z-index: 2 }
  .as-view[hidden] { display: none } .as-view pre { margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: .78rem/1.5 var(--font-mono) }
  @media (prefers-reduced-motion: reduce) { .as-typing i { animation: none } }`;

  // A deliberately small Markdown renderer: everything is escaped first, then a few safe forms are allowed back.
  function md(src, onCite) {
    const lines = esc(src).split("\n"), out = [];
    let list = null, para = [], i = 0;
    const inline = (t) => t.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>").replace(/_([^_\n]{2,})_/g, "<i>$1</i>")
      .replace(/\[S(\d{1,2})\]/g, (m, n) => `<button type="button" class="as-cite" data-cite="${n}" aria-label="Source ${n}">S${n}</button>`);
    const flush = () => { if (para.length) { out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; } if (list) { out.push(`</${list}>`); list = null; } };
    while (i < lines.length) {
      const l = lines[i];
      if (/^```/.test(l)) { flush(); const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); out.push(`<pre>${buf.join("\n")}</pre>`); i++; continue; }
      if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        flush(); const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
        const head = cells(l); i += 2; const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
        out.push(`<div class="tw"><table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const h = l.match(/^(#{1,4})\s+(.*)/);
      if (h) { flush(); out.push(`<${h[1].length <= 2 ? "h3" : "h4"}>${inline(h[2])}</${h[1].length <= 2 ? "h3" : "h4"}>`); i++; continue; }
      const li = l.match(/^\s*([-*]|\d+\.)\s+(.*)/);
      if (li) { if (para.length) { out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; } const t = /\d/.test(li[1]) ? "ol" : "ul";
        if (list !== t) { if (list) out.push(`</${list}>`); out.push(`<${t}>`); list = t; } out.push(`<li>${inline(li[2])}</li>`); i++; continue; }
      if (!l.trim()) { flush(); i++; continue; }
      if (list) { out.push(`</${list}>`); list = null; }
      para.push(l); i++;
    }
    flush();
    return out.join("");
  }

  const S = { me: null, scope: [], uploads: [], history: [], busy: false };
  // Kept per person, and dropped on sign-out, so the next person on this browser never sees earlier answers.
  const key = () => "askSentinel:" + (S.me?.actor_id || "?");
  const store = { get() { try { return JSON.parse(sessionStorage.getItem(key()) || "null"); } catch (_) { return null; } },
    set(v) { try { sessionStorage.setItem(key(), JSON.stringify(v)); } catch (_) {} } };
  document.addEventListener("click", (e) => { if (e.target.closest("#signOut")) try { Object.keys(sessionStorage).filter((k) => k.startsWith("askSentinel:")).forEach((k) => sessionStorage.removeItem(k)); } catch (_) {} }, true);

  async function api(path, opt = {}) {
    const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opt });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status} ${r.statusText}`);
    return body;
  }

  function mount() {
    const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    const fab = document.createElement("button");
    fab.className = "as-fab"; fab.type = "button"; fab.id = "asFab"; fab.setAttribute("aria-expanded", "false"); fab.setAttribute("aria-controls", "asPanel");
    fab.innerHTML = `<span class="as-logo" aria-hidden="true"></span><span>Ask Sentinel</span>`;
    const p = document.createElement("section");
    p.className = "as-panel"; p.id = "asPanel"; p.hidden = true; p.setAttribute("aria-label", "Ask Sentinel");
    p.innerHTML = `
      <div class="as-head"><span class="as-mark as-logo" aria-hidden="true"></span><div class="t"><b>Ask Sentinel</b>
        <small id="asWho">Answers from what your role can see, with sources</small></div>
        <button class="as-x" type="button" id="asNew" title="New conversation" aria-label="New conversation">${ico("doc", 16)}</button>
        <button class="as-x" type="button" id="asClose" aria-label="Close" style="margin-left:0">${ico("close", 16)}</button></div>
      <details class="as-scope"><summary>${ico("shield", 14)}<span id="asScopeLine">What I can search for you</span></summary><ul id="asScope"></ul></details>
      <div class="as-log" id="asLog" aria-live="polite"></div>
      <div class="as-foot"><div class="as-docs" id="asDocs"></div>
        <div class="as-row"><button class="as-ib" type="button" id="asAttach" title="Add a document (.txt, .md, .csv, .json, .log)" aria-label="Add a document">${ico("clip")}</button>
          <textarea id="asQ" rows="1" placeholder="Ask about an alert, a card, an approval, a policy…" aria-label="Your question"></textarea>
          <button class="as-ib go" type="button" id="asSend" aria-label="Send">${ico("send")}</button></div>
        <input type="file" id="asFile" accept=".txt,.md,.csv,.json,.log,.tsv,text/*" hidden>
        <div class="as-hint">Read-only: it can explain and draft, never block, approve or file. Enter to send · Shift+Enter for a new line.</div></div>
      <div class="as-view" id="asView" hidden><div class="as-head"><span class="as-mark" style="background:var(--info)">${ico("doc", 16)}</span>
        <div class="t"><b id="asViewT"></b><small id="asViewK"></small></div><button class="as-x" type="button" id="asViewX" aria-label="Back to the conversation">${ico("close", 16)}</button></div><pre id="asViewB"></pre></div>`;
    document.body.append(fab, p);
    const $ = (id) => document.getElementById(id);
    const open = (v) => { p.hidden = !v; fab.setAttribute("aria-expanded", String(v)); if (v) { $("asQ").focus(); scrollEnd(); } };
    fab.onclick = () => open(true); $("asClose").onclick = () => open(false);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !p.hidden) { if (!$("asView").hidden) $("asView").hidden = true; else open(false); } });
    $("asNew").onclick = () => { S.history = []; store.set({ history: [] }); render(); };
    $("asSend").onclick = send;
    $("asQ").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    $("asQ").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });
    $("asAttach").onclick = () => $("asFile").click();
    $("asFile").onchange = upload;
    $("asViewX").onclick = () => ($("asView").hidden = true);
    $("asLog").addEventListener("click", onLogClick);
    $("asDocs").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-rm]"); if (!b) return;
      try { await api(`/v2/assistant/uploads/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); S.uploads = S.uploads.filter((u) => u.doc_id !== b.dataset.rm); docs(); }
      catch (err) { note(err.message); }
    });
  }

  const scrollEnd = () => { const l = document.getElementById("asLog"); if (l) l.scrollTop = l.scrollHeight; };

  function docs() {
    document.getElementById("asDocs").innerHTML = S.uploads.map((u) => `<span title="${esc(u.name)} · ${u.chars.toLocaleString()} characters${u.instruction_like_spans?.length ? " · contains instruction-like text, read as data" : ""}">${ico("doc", 12)}<em>${esc(u.name)}</em><button type="button" data-rm="${esc(u.doc_id)}" aria-label="Remove ${esc(u.name)}">×</button></span>`).join("");
  }

  function botHTML(m, ix) {
    if (m.error) return `<div class="as-msg bot"><div class="b" style="border-color:var(--danger);color:var(--danger)">${esc(m.error)}</div></div>`;
    const r = m.result, g = r.guardrails || {};
    const SUP = { supported: "Supported by sources", partial: "Partly supported", unsupported: "Unverified", not_found: "Not found in what you can see", refused: "Outside what I can do" };
    const refs = r.sources.length ? `<div class="as-refs"><span class="h">Sources</span>${r.sources.map((s) => `
      <button type="button" class="as-ref ${s.cited ? "cited" : ""}" data-src="${esc(s.id)}" data-link="${esc(s.link)}"><span class="m">${s.marker}</span><span class="tt">${esc(s.title)}</span>
      <span class="k">${esc(s.kind)}${s.cited ? " · cited" : ""} · ${esc(s.found_by.join(" + "))}</span></button>`).join("")}</div>` : "";
    const withheld = r.withheld.length ? `<div class="as-withheld">${ico("lock", 14)}<span>Some matching material is outside your role: ${r.withheld.map((w) => `<b>${esc(w.what)}</b> <span class="mono">(${esc(w.permission)})</span>`).join(", ")}.</span></div>` : "";
    const inj = [...(g.instruction_like_text_in_question || []), ...(g.instruction_like_text_in_sources || [])];
    return `<div class="as-msg bot" data-ix="${ix}"><span class="as-sup ${esc(r.support)}">${esc(SUP[r.support] || r.support)}</span>
      <div class="b">${md(r.answer)}</div>${withheld}${refs}
      <div class="as-guard">${ico("shield", 13)}<span>Searched ${g.sources_searched} sources your role can see; ${g.sources_withheld_by_role} withheld by role${g.citations_removed ? ` · ${g.citations_removed} invalid citation(s) removed` : ""}${inj.length ? ` · instruction-like text treated as data` : ""}${(r.notes || []).length ? ` · ${esc(r.notes.join("; "))}` : ""} · ${r.model ? esc(r.model) : "no model"} · ${r.tokens} tokens · ${r.latency_s} s</span></div>
      <div class="as-tools"><button type="button" data-copy="${ix}">${ico("copy", 13)}Copy</button><button type="button" data-save="${ix}">${ico("save", 13)}Save as brief (.md)</button></div></div>`;
  }

  function render() {
    const log = document.getElementById("asLog");
    if (!S.history.length) {
      const role = S.me?.actor_role;
      log.innerHTML = `<div class="as-msg bot"><div class="b"><p>Hello ${esc(S.me?.actor_id || "")}. I answer from the investigations, audit trail, live graph and documents your role (<b>${esc(ROLE_NAME[role] || role)}</b>) can see, and every answer links to its sources.</p>
        <p>Add your own document with the paper-clip, and I will read it alongside everything else. Only you can see it.</p></div></div>
        <div class="as-sugg">${(SUGGEST[role] || SUGGEST.analyst).map((q) => `<button type="button" data-ask="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
      return;
    }
    log.innerHTML = S.history.map((m, i) => m.role === "user" ? `<div class="as-msg user"><div class="b">${esc(m.text)}</div></div>` : botHTML(m, i)).join("")
      + (S.busy ? '<div class="as-msg bot"><div class="b as-typing" aria-label="Searching"><i></i><i></i><i></i></div></div>' : "");
    scrollEnd();
  }

  function note(text) { S.history.push({ role: "assistant", error: text }); render(); }

  async function send(q) {
    const box = document.getElementById("asQ");
    const question = (typeof q === "string" ? q : box.value).trim();
    if (!question || S.busy) return;
    box.value = ""; box.style.height = "auto";
    const history = S.history.filter((m) => !m.error).map((m) => ({ role: m.role, text: m.role === "user" ? m.text : m.result.answer }));
    S.history.push({ role: "user", text: question }); S.busy = true; render();
    document.getElementById("asSend").disabled = true;
    try {
      const result = await api("/v2/assistant/ask", { method: "POST", body: JSON.stringify({ question, history }) });
      S.history.push({ role: "assistant", result });
    } catch (e) { S.history.push({ role: "assistant", error: e.message }); }
    S.busy = false; document.getElementById("asSend").disabled = false; render();
    store.set({ history: S.history.slice(-20) });
  }

  async function upload(e) {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    if (f.size > 400000) return note(`${f.name} is ${(f.size / 1000).toFixed(0)} KB; the limit is 400 KB of text.`);
    try {
      const text = await f.text();
      const meta = await api("/v2/assistant/uploads", { method: "POST", body: JSON.stringify({ name: f.name, text }) });
      S.uploads.push(meta); docs();
      S.history.push({ role: "assistant", result: { answer: `Added **${meta.name}** (${meta.chars.toLocaleString()} characters). I will read it with everything else your role can see; only you can see it.`
        + (meta.instruction_like_spans.length ? `\n\nIt contains wording aimed at an automated system (“${meta.instruction_like_spans[0]}”). I will treat that as data, not instructions.` : ""),
        support: "supported", sources: [], withheld: [], guardrails: { sources_searched: 0, sources_withheld_by_role: 0 }, notes: [], model: null, tokens: 0, latency_s: 0 } });
      render();
    } catch (err) { note(err.message); }
  }

  async function onLogClick(e) {
    const ask = e.target.closest("[data-ask]"); if (ask) return send(ask.dataset.ask);
    const cite = e.target.closest("[data-cite]");
    if (cite) { const m = cite.closest(".as-msg"); const ref = m && m.querySelectorAll(".as-ref")[+cite.dataset.cite - 1]; if (ref) { ref.scrollIntoView({ block: "nearest" }); ref.focus(); } return; }
    const ref = e.target.closest("[data-src]");
    if (ref) {
      const link = ref.dataset.link;
      if (link && !link.startsWith("/v2/")) { // a page in the app: go there, keep the conversation
        if (link.split("#")[0] === location.pathname) { location.hash = link.split("#")[1] || ""; } else location.href = link; return;
      }
      try {
        const s = await api(`/v2/assistant/sources/${encodeURIComponent(ref.dataset.src)}`);
        document.getElementById("asViewT").textContent = s.title; document.getElementById("asViewK").textContent = s.kind;
        document.getElementById("asViewB").textContent = s.text; document.getElementById("asView").hidden = false;
      } catch (err) { // live graph facts are not stored, so show what the answer carried
        const m = S.history[+ref.closest(".as-msg").dataset.ix]?.result?.sources.find((x) => x.id === ref.dataset.src);
        document.getElementById("asViewT").textContent = m ? m.title : ref.dataset.src; document.getElementById("asViewK").textContent = m ? `${m.kind} · read live from the graph when you asked` : err.message;
        document.getElementById("asViewB").textContent = m ? m.snippet + "…" : ""; document.getElementById("asView").hidden = false;
      }
      return;
    }
    const cp = e.target.closest("[data-copy]");
    if (cp) { const r = S.history[+cp.dataset.copy].result; try { await navigator.clipboard.writeText(r.answer); cp.lastChild.textContent = "Copied"; } catch (_) { cp.lastChild.textContent = "Copy failed"; } return; }
    const sv = e.target.closest("[data-save]");
    if (sv) {
      const ix = +sv.dataset.save, r = S.history[ix].result, q = S.history[ix - 1]?.text || "";
      const doc = `# ${q}\n\n_Ask Sentinel · ${S.me.actor_id} (${ROLE_NAME[S.me.actor_role] || S.me.actor_role}) · ${new Date().toISOString().slice(0, 16).replace("T", " ")} · ${r.support}_\n\n${r.answer}\n\n## Sources\n\n`
        + r.sources.map((s) => `- **${s.marker}** ${s.title} (${s.kind})${s.link ? ` — ${s.link.startsWith("/") ? location.origin + s.link : s.link}` : ""}`).join("\n")
        + (r.withheld.length ? `\n\n_Not included, outside this role: ${r.withheld.map((w) => w.what).join("; ")}._` : "") + "\n";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([doc], { type: "text/markdown" }));
      a.download = `sentinel-brief-${new Date().toISOString().slice(0, 10)}.md`; document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    }
  }

  async function boot() {
    try {
      const sc = await api("/v2/assistant/scope");
      S.me = sc.viewer; S.scope = sc.scope; S.uploads = sc.uploads || [];
    } catch (_) { return; } // not signed in, or the role may not use the assistant: no button at all
    mount();
    const on = S.scope.filter((x) => x.granted).length;
    document.getElementById("asWho").textContent = `${ROLE_NAME[S.me.actor_role] || S.me.actor_role} · answers from what your role can see, with sources`;
    document.getElementById("asScopeLine").textContent = `I can search ${on} of ${S.scope.length} kinds of record for you`;
    document.getElementById("asScope").innerHTML = S.scope.map((x) => `<li><span class="${x.granted ? "on" : "off"}">${x.granted ? "✓" : "–"}</span><span class="${x.granted ? "" : "off"}">${esc(x.what)}${x.granted ? "" : ` <span class="mono">(needs ${esc(x.permission)})</span>`}</span></li>`).join("");
    const saved = store.get(); if (saved && Array.isArray(saved.history)) S.history = saved.history;
    docs(); render();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
