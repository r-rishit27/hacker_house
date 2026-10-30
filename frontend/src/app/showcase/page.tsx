"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Panel } from "@/components/ui/Panel";
import { RouteBadge } from "@/components/ui/RouteBadge";
import { VerdictChip } from "@/components/ui/VerdictChip";
import { StatusChip } from "@/components/ui/StatusChip";
import { PatternChip } from "@/components/ui/PatternChip";
import { ProbabilityMeter } from "@/components/ui/ProbabilityMeter";
import { EvidenceItem } from "@/components/ui/EvidenceItem";
import type { EvidencePostedPayload } from "@/lib/generated/contract";
import { usd } from "@/lib/format";

/**
 * Design-system showcase. Not a product screen — it exists so the tokens and
 * primitives can be reviewed in both themes before any feature work starts,
 * which is the point of building the design system first.
 */

const POSTINGS: EvidencePostedPayload[] = [
  {
    step: 3, feature: "risk_85_100", present: true, group: "risk_score",
    claim: "The bank's model scored this transaction 0.90, in its highest band.",
    source: "graph", ref: "query:txn_detail(txn_id=3503878)", entity_ids: ["3503878"],
    lr: 18.76, log_lr: 0.93, p_before: 0.25, p_after: 0.46, capped: false,
  },
  {
    step: 3, feature: "amt_seen_before_on_card", present: true, group: "amount",
    claim: "53 prior charges on this card fall within $0.50 of this amount.",
    source: "graph", ref: "query:amount_band_probe(card_id=C07987-K2, amt=99.92, tol=0.5)",
    entity_ids: ["C07987-K2"], lr: 0.93, log_lr: -0.08, p_before: 0.46, p_after: 0.44, capped: false,
  },
  {
    step: 3, feature: "device_new", present: false, group: "device",
    claim: "The identity record does not mark this device as new for the account.",
    source: "graph", ref: "query:device_novelty(txn_id=3503878)", entity_ids: ["3503878"],
    lr: 0.96, log_lr: -0.04, p_before: 0.44, p_after: 0.43, capped: false,
  },
  {
    step: 4, feature: "region_well_established", present: true, group: "region",
    claim: "The card has 264 prior transactions in billing region 264.0, first seen 2016-07-14.",
    source: "graph", ref: "query:region_novelty(card_id=C07987-K2, region=264.0, as_of=2016-12-01 17:28:53)",
    entity_ids: ["C07987-K2", "264.0"], lr: 1.21, log_lr: 0.19, p_before: 0.43, p_after: 0.42, capped: true,
  },
  {
    step: 4, feature: "policy_r1", present: true, group: "document",
    claim: "Policy R1 bars a block while the case rests on a single signal below 0.70.",
    source: "document", ref: "doc:POL-R1#verify_before_block", entity_ids: [],
    lr: 1.0, log_lr: 0.0, p_before: 0.42, p_after: 0.42, capped: false,
  },
];

export default function DesignSystemPage() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  function toggle() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-overline uppercase text-fg-subtle">Sentinel design system</p>
          <h1 className="text-h1">Tokens and primitives</h1>
        </div>
        <Button variant="secondary" onClick={toggle}>
          {theme === "light" ? "Dark theme" : "Light theme"}
        </Button>
      </header>

      <Panel eyebrow="§9" title="Buttons">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary">Investigate</Button>
          <Button variant="secondary">Open case</Button>
          <Button variant="ghost">Dismiss</Button>
          <Button variant="danger">Block card</Button>
          <Button variant="authority">Approve as fraud manager</Button>
          <Button variant="primary" loading>Running</Button>
          <Button variant="secondary" disabled>Disabled</Button>
          <Button variant="secondary" size="sm">Small</Button>
        </div>
      </Panel>

      <Panel eyebrow="§3.3" title="Approval routes">
        <div className="space-y-2">
          <p className="text-small text-fg-muted">
            Route is authority, not danger. Solid violet is reserved for L2 across the entire
            product, so a filled chip always means the action is out of the analyst&rsquo;s hands.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <RouteBadge route="auto" />
            <RouteBadge route="L1" />
            <RouteBadge route="L2" />
            <span className="text-small text-fg-subtle">
              BLOCK_CARD at {usd(2500)} routes L1; at {usd(2500.01)} it routes L2.
            </span>
          </div>
        </div>
      </Panel>

      <Panel eyebrow="§3.1 – §3.2" title="Verdict, status, pattern">
        <div className="flex flex-wrap items-center gap-2">
          <VerdictChip verdict="fraud" />
          <VerdictChip verdict="uncertain" />
          <VerdictChip verdict="legitimate" />
          <StatusChip status="open" />
          <StatusChip status="escalated" />
          <StatusChip status="closed_fraud" />
          <StatusChip status="closed_legitimate" />
          <PatternChip pattern="card_testing" />
          <PatternChip pattern="out_of_region_use" />
          <PatternChip pattern="undocumented" description="Repeated small-value refunds across unrelated cards sharing one recipient domain." />
          <PatternChip pattern="none" />
          <Chip tone="neutral" fill="ghost">simulated</Chip>
        </div>
      </Panel>

      <Panel eyebrow="§11.1" title="Probability meter">
        <div className="max-w-md space-y-4">
          {[0.08, 0.32, 0.55, 0.9].map((p) => (
            <ProbabilityMeter key={p} value={p} prior={0.25} verdictLabel={p > 0.7 ? "fraud" : p < 0.15 ? "legitimate" : "uncertain"} />
          ))}
          <p className="text-caption text-fg-subtle">
            The caret marks the ledger prior. A score-triggered alert starts at 0.25 — not at the
            model&rsquo;s risk score.
          </p>
        </div>
      </Panel>

      <Panel eyebrow="§3.4" title="Evidence">
        <ul className="-my-2.5">
          {POSTINGS.map((p) => (
            <EvidenceItem key={`${p.feature}-${p.step}`} posting={p} />
          ))}
        </ul>
      </Panel>
    </main>
  );
}
