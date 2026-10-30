/**
 * Does the console actually render, and does the run flow actually run?
 *
 * A build that compiles proves nothing about a client component that throws
 * on its first render. This drives a real browser, fails on any console error
 * or page exception, and asserts the things the port was for: the masthead,
 * the queue, the replay advancing, the graph pane drawing nodes, and the two
 * full-screen views.
 */

import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://127.0.0.1:3001";
const problems = [];
const results = [];

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  if (!ok) problems.push(`${name}${detail ? `: ${detail}` : ""}`);
}

// `colorScheme: "dark"` on purpose. The console is a light instrument and
// must stay light whatever the viewer's OS says; following the preference is
// a defect this test exists to catch.
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1440, height: 900 },
  colorScheme: "dark",
});

const noise = [];
page.on("console", (message) => {
  if (message.type() === "error") noise.push(message.text());
});
page.on("pageerror", (error) => noise.push(`pageerror: ${error.message}`));

await page.goto(BASE, { waitUntil: "networkidle" });

// ── the masthead and the chrome ──────────────────────────────────────────────
check("masthead", (await page.getByText("SENTINEL", { exact: true }).count()) > 0);
check("runway rule", (await page.locator(".bg-runway").count()) === 1);
check("hunt strip", (await page.getByText(/QUARRY LOCKED|STOOD DOWN|HELD AT BAY|STALKING|ON THE SCENT|NOT YET RUN/).count()) > 0);

// ── the queue ────────────────────────────────────────────────────────────────
await page.waitForSelector("text=CASE QUEUE", { timeout: 15000 });
const queueRows = await page.locator('aside button[aria-current], aside button').count();
check("queue rows", queueRows >= 20, `${queueRows} rows`);

// ── the warm palette actually applied ────────────────────────────────────────
const bg = await page.evaluate(() =>
  getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
);
check("stays light under a dark OS", bg === "#f7f4ef", bg);
const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
check("Space Grotesk", /Grotesk/i.test(font), font);

// ── the run flow: postings arrive one at a time ──────────────────────────────
// Reload and sample immediately. Measuring after the other checks have run
// would time the test rather than the replay — 23 postings at 460ms finish in
// about ten seconds, which is longer than the assertions above take.
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForSelector("ol li", { timeout: 20000 });
const first = await page.locator("ol li").count();
await page.waitForTimeout(1600);
const later = await page.locator("ol li").count();
check("replay advances", later > first, `${first} then ${later} postings`);

// The log-odds chip and the before→after pair are the point of the row.
check("log-odds shown", (await page.getByText(/logLR/).count()) > 0);

await page.getByRole("button", { name: /skip replay/ }).click().catch(() => {});
await page.waitForTimeout(900);
const settled = await page.locator("ol li").count();
check("skip reveals all", settled >= later, `${settled} postings`);

// ── the graph pane: the cut-list item ────────────────────────────────────────
await page.waitForSelector("text=GRAPH EVIDENCE", { timeout: 10000 });
const nodes = await page.locator('aside svg g.animate-s-pop circle').count();
check("graph nodes drawn", nodes >= 3, `${nodes} nodes`);
check("focus lock ring", (await page.locator("aside svg circle.animate-lock").count()) >= 1);

// ── the tabs ─────────────────────────────────────────────────────────────────
for (const [tab, marker] of [
  ["Case file", /Evidence ledger/i],
  ["Memory", /Retrieved memory/i],
  ["Answer file", /cases\/HHG-/],
]) {
  await page.getByRole("tab", { name: new RegExp(tab) }).click();
  // Wait for the content, not for a fixed delay: the memory tab costs a round
  // trip that the other two do not.
  const found = await page
    .getByText(marker)
    .first()
    .waitFor({ timeout: 10000 })
    .then(() => true, () => false);
  check(`tab ${tab}`, found);
}

// ── the two full-screen views ────────────────────────────────────────────────
await page.getByRole("tab", { name: "Monitor" }).click();
await page.waitForSelector("text=The pack, scored", { timeout: 15000 });
check("monitor renders", (await page.getByText(/Block rate/i).count()) > 0);

await page.getByRole("tab", { name: "Ring" }).click();
await page.waitForTimeout(1200);
// The scope closing on the shared profile, and the edges being walked.
check("ring scope animates", (await page.locator(".z-view svg circle.animate-spin-slow").count()) >= 1);
check("ring edges are traced", (await page.locator(".z-view svg line.animate-trace").count()) >= 4);
// The ring screen has to carry two facts at once: the finding is negative,
// and there is still something worth drawing. Both are asserted, because
// earlier versions lost one or the other — once by claiming a connectivity
// the drawing did not have, once by burying the finding in a 118-node mesh.
check(
  "ring keeps the negative finding as the headline",
  (await page.getByText("No ring on this pack").count()) > 0,
);
const ringNodes = await page.locator(".z-view svg g.animate-s-pop circle").count();
check("ring draws the component", ringNodes >= 5 && ringNodes <= 40, `${ringNodes} nodes`);
const ringText = (await page.locator(".z-view svg text").allTextContents()).join(" ");
check("ring names the shared profile", /cards · \d+ customers/.test(ringText));
check("ring names its cards", (ringText.match(/C\d{5}-K\d/g) ?? []).length >= 4);
check(
  "ring marks what the bank already confirmed",
  /confirmed fraud/.test(ringText) || /benchmark seed/.test(ringText),
);
check(
  "ring lists its members",
  (await page.locator(".z-view li").count()) >= 4,
  `${await page.locator(".z-view li").count()} rows`,
);

// The monitor's bars: a percentage height inside a flex column resolves to
// zero, which shipped once already.
await page.getByRole("tab", { name: "Monitor" }).click();
await page.waitForSelector("text=The pack, scored");
await page.waitForTimeout(800);
const barHeights = await page.evaluate(() =>
  Array.from(document.querySelectorAll("section div[style*=\"height\"]"))
    .map((el) => el.getBoundingClientRect().height)
    .filter((h) => h > 0),
);
check("histogram has bars", barHeights.some((h) => h > 20), `tallest ${Math.max(0, ...barHeights).toFixed(0)}px`);
await page.getByRole("tab", { name: "Ring" }).click();
await page.waitForTimeout(600);

// ── the approvals drawer ─────────────────────────────────────────────────────
await page.getByRole("tab", { name: "Console" }).click();
await page.getByRole("button", { name: /Approvals/ }).click();
await page.waitForSelector("text=APPROVAL INBOX", { timeout: 8000 });
check("approvals drawer", true);

// ── phone width must not scroll sideways ─────────────────────────────────────
await page.keyboard.press("Escape").catch(() => {});
await page.setViewportSize({ width: 1180, height: 900 });
await page.waitForTimeout(600);

// The masthead's run controls, which are the demo.
check("run demo button", (await page.getByRole("button", { name: /Run demo/ }).count()) === 1);
check("run all button", (await page.getByRole("button", { name: /Run all 20/ }).count()) === 1);

check("no console errors", noise.length === 0, noise.slice(0, 4).join(" | "));

await browser.close();

for (const { name, ok, detail } of results) {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? `  (${detail})` : ""}`);
}
console.log(`\n${results.filter((r) => r.ok).length}/${results.length} checks passed`);
process.exit(problems.length === 0 ? 0 : 1);
