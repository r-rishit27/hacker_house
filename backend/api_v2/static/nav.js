// Shared masthead for the v2 pages: who is signed in, the pages their role may open, sign out.
// The server enforces every permission; this only decides which links to show.
window.SentinelNav = (function () {
  const PAGES = [
    { href: "/", label: "Console", need: "queue.view" },
    { href: "/portal#approvals", label: "Approvals", need: "approve.L1" },
    { href: "/portal#access", label: "Access & roles", need: "users.view" },
    { href: "/regulator", label: "Regulator overview", need: "regulator.view" },
  ];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ROLE_NAME = { analyst: "Analyst", team_lead: "Team lead", fraud_manager: "Fraud manager", regulator: "Regulator" };
  async function me() {
    const r = await fetch("/v2/me", { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash); throw new Error("signed out"); }
    return r.json();
  }
  function render(m, here) {
    const nav = document.getElementById("nav"), who = document.getElementById("who");
    if (!nav || !who) return;
    const perms = new Set(m.permissions || []);
    nav.innerHTML = PAGES.filter((p) => perms.has(p.need))
      .map((p) => `<a href="${p.href}" ${p.href === here ? 'aria-current="page"' : ""}>${p.label}</a>`).join("");
    who.innerHTML = m.authenticated
      ? `<span>${esc(m.actor_id)}</span><span class="role-badge role-${esc(m.actor_role)}">${esc(ROLE_NAME[m.actor_role] || m.actor_role)}</span>
         <button class="btn" type="button" id="signOut">Sign out</button>`
      : '<span class="muted">Demo mode · not signed in</span>';
    const so = document.getElementById("signOut");
    if (so) so.addEventListener("click", async () => { await fetch("/v2/logout", { method: "POST", credentials: "same-origin" }); location.href = "/login"; });
  }
  return { me, render, ROLE_NAME, esc };
})();
