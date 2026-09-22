# Sentinel demo video: script

**Length:** about 4 minutes 45 seconds · **Live app:** https://hackerhouse.collabup.co.in
**Team CollabUp** · TigerGraph Agentic Fraud Investigation challenge, Hacker House Goa

The judges want to see an agent move from an uncertain fraud signal to a
defensible action, within policy, with its reasoning shown. This script is
built around one contrast that appears in the live data:

> **HHG-004** is real fraud, but the bank's model scored it **0.34**.
> **HHG-007** is innocent, but the model scored it **0.87**.
> Sentinel gets both right.

Every number below was checked against the live API before this was written.
If you re-run cases before recording, check the numbers on screen and read
those instead.

---

## Before you record

**1. Wake the graph.** The TigerGraph workspace auto-stops when idle and takes
about 45 seconds to start. Open this link and refresh it until it says ready:
https://apihacker.collabup.co.in/api/ready
Do this **within 10 minutes of recording**, or the first case will hang on camera.

**2. Set up the browser.**
- Screen at 1920×1080, browser zoom 110–125% so text reads on small screens
- Full screen (F11), bookmarks bar hidden, notifications off
- Role switcher set to **Analyst**

**3. Rehearse the clicks once** without recording, especially the approval
flow in Scene 3. It's the one part that needs exact clicks.

**4. Replay, don't run live.** A live investigation calls the model and takes
about 17 seconds. The recorded replay shows the same steps and never fails on
camera. The **Run demo** button plays Acts I–III automatically with captions.
It's useful as a fallback, but driving it yourself gives better pacing.

**Recording tools:** OBS Studio or Loom to record, Clipchamp (built into Windows)
to trim. Record the voiceover separately if you can, since it's easier to edit.

---

## The script

Each scene gives the time, what's **on screen**, what to **click**, and what
to **say**. The voiceover runs about 650 words at a relaxed pace.

---

### Scene 1: The hook · 0:00–0:25

**On screen:** the console, with the case queue of 20 alerts.

**Say:**
> A fraud analyst gets an alert: a transaction scored 0.87. Do you block the
> card?
>
> Here's the problem. In this bank's data, most high-scoring transactions turn
> out to be legitimate, and some real fraud scores almost zero.
>
> This is Sentinel, an AI agent on TigerGraph that investigates the alert, and
> knows when *not* to block. Built by Team CollabUp.

---

### Scene 2: How it works · 0:25–0:50

**On screen:** stay on the queue. Optionally cut to
`docs/medium/img/tech-stack.png` for 3 seconds.

**Say:**
> These are the twenty benchmark alerts. Sentinel splits every decision three
> ways.
>
> **Facts** come from the graph: twenty-six GSQL queries on TigerGraph.
> **Belief** comes from a probability ledger, fitted on the bank's own closed
> cases.
> And **decisions** come from the fraud policy, written as tested code.
>
> The language model, GPT-5.4 mini, plans and explains. It never picks an
> action or an approval route.

---

### Scene 3: Act I, a real fraud · 0:50–2:00

**Click:** open **HHG-004** and start the replay. Let the timeline stream.

**Say** (as the timeline runs):
> First, a real fraud. The customer says: *"I never made this $128 purchase."*
>
> Each step on the timeline is the agent questioning the graph, and each
> finding moves the probability line. A device this account has never used.
> An amount more than two standard deviations above what this card normally
> spends.

**Point at:** the probability line reaching **0.99**, then the risk score.

> The bank's own model scored this transaction 0.34. It would have waved it
> through. Sentinel lands at 0.99.
>
> Every piece of evidence carries the exact query that produced it, so an
> analyst can re-run any line.

**Point at:** the next best actions, **BLOCK_CARD** with route **L1**.

> It recommends blocking the card. But look at the route: L1. That needs a
> team lead. Watch what happens when I try to execute it as an analyst.

**Click:** execute **BLOCK_CARD** as Analyst. The request is refused.

> Refused, and it tells me who can approve it.

**Click:** switch the role to **Team lead**, open the approval, approve it.

> As team lead, I approve. Now it executes, and both attempts, the refused one
> and the approved one, are in the audit log.

---

### Scene 4: Act II, the trap · 2:00–2:45

**Click:** open **HHG-007** and replay it.

**Say:**
> Now the trap. The bank's model scored this one 0.87, its highest band. Most
> systems block here.
>
> Sentinel starts lower, because a score is a reason to look, not a verdict.
> Then the graph pushes back. The card was physically present. It was used in
> a billing region with a long history on this card. And the amount is normal
> for it.

**Point at:** the probability falling to **0.08**, and **CLOSE_NO_FRAUD**.

> The probability falls to 0.08, and the recommendation is: close it, no
> fraud.
>
> The model said 0.87. The graph said no, and the evidence says why.

---

### Scene 5: The recommendation changes · 2:45–3:25

**Click:** open **HHG-006**. Show the **before** and **after** recommendations
side by side.

**Say:**
> Sometimes the evidence isn't enough, so Sentinel asks for more.
>
> Here, its first recommendation was to block the card and file a suspicious
> activity report. Instead of acting on that, it requested step-up
> authentication, a one-time passcode. The cardholder passed.
>
> So its final recommendation drops the block and the report. It opens a case
> and hands it to a human analyst. Both recommendations are recorded, along
> with what changed between them.

> *This scene is the judges' "next best action" criterion, worth 25% of the
> score. Don't cut it.*

---

### Scene 6: The regulatory report · 3:25–3:45

**Click:** open **HHG-010**, then the **SAR** tab.

**Say:**
> When the policy does require a regulatory filing, here because the exposure
> is just over a thousand dollars, Sentinel drafts the Suspicious Activity
> Report: who, what, when, where, and why it's suspicious. Filing it routes to
> L2, a fraud manager.

---

### Scene 7: The ring that isn't there · 3:45–4:10

**Click:** open the **Ring** view.

**Say:**
> Some fraud only shows up across cards. Here, one device fingerprint links
> twelve cards from twelve customers, and seven were already confirmed as
> fraud.
>
> But the policy defines a ring at one hop, and at one hop nothing connects.
> Sentinel reports that honestly instead of inventing a ring.

> *Check the ring figures on screen before recording, and read them as shown.*

---

### Scene 8: Memory and the scorecard · 4:10–4:35

**Click:** the **Memory** tab on any case, then the **Monitor** view.

**Say:**
> Every finished case is written back into TigerGraph, so later
> investigations can find it. Here, it retrieved the bank's past cases on the
> same card.
>
> Across all twenty alerts, every answer file validates against the live
> graph. And Sentinel blocks only two of the twenty, instead of blocking
> everything.

> *Point at the Monitor tiles as you speak. If you want to quote the
> "recommendation moved" figure, read it from the screen.*

---

### Scene 9: Close · 4:35–4:50

**On screen:** back to the console, or a title card with the URL.

**Say:**
> Sentinel: facts from the graph, belief from evidence, decisions from
> policy.
>
> Try it live at hackerhouse.collabup.co.in. Built on TigerGraph, by Team
> CollabUp.

---

## Need it shorter?

| Target | Cut | Keeps |
|---|---|---|
| **~4:00** | Scene 6 (the SAR) | all of the scored criteria |
| **~3:30** | Scenes 6 and 7 | fraud, the trap, the changed recommendation, permissions, memory |
| **~3:00** | Also cut Scene 2 to one sentence, and the Memory half of Scene 8 | the core argument |

**Never cut Scenes 3, 4 or 5.** Together they show the fraud caught, the
innocent cleared, and the recommendation changing as evidence arrives. That's
the whole pitch.

---

## What each scene proves to the judges

| Judging criterion | Weight | Where the video shows it |
|---|---|---|
| Investigation accuracy | 25% | Scenes 3 and 4: fraud the model missed, and a false alarm it would have blocked |
| Next best action | 25% | Scene 5 (the recommendation changes), Scene 3 (approval routes) |
| Case summary and explainability | 10% | Evidence with its query refs (Scene 3), the SAR (Scene 6) |
| Agentic design and engineering | 15% | Scene 2, the permission boundary (Scene 3), memory (Scene 8) |
| Innovation | 15% | The calibrated ledger (Scene 4), the honest ring negative (Scene 7) |
| Demo quality | 10% | The whole thing: one clear contrast, told end to end |

---

## Key numbers, for reference

| Case | What it shows | Model score | Sentinel | Final actions |
|---|---|---|---|---|
| HHG-004 | Fraud the model missed | 0.34 | **fraud, 0.99** | BLOCK_CARD (L1), CREATE_CASE |
| HHG-007 | The trap | 0.87 | **legitimate, 0.08** | CLOSE_NO_FRAUD |
| HHG-006 | The recommendation changes | 0.25 | uncertain, 0.56 | before: block + report · after: case + escalate |
| HHG-010 | The SAR | 0.90 | fraud, 0.95 | includes FILE_REPORT (L2), exposure $1,000.03 |
| HHG-014 | Ring view (analyst request) | n/a | uncertain, 0.48 | CREATE_CASE |

Across all 20 alerts: 8 fraud · 9 uncertain · 3 legitimate · 2 blocks · 2 SARs.
