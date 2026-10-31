# Sentinel — the system on one page

> Facts come from TigerGraph, belief from detectors and history checks, decisions from rules and a policy engine.
> The model only scores what the rules cannot settle, a person approves every block and report, **Ask Sentinel**
> answers anyone's questions from only what their role may see, with sources, and new banks plug in through
> **bank onboarding**: any schema, the same detectors.

Interactive version (click any box for what it does and where it lives):
[claude.ai/artifact/JYEWEZyCDC1yXwjtYhPzCC](https://claude.ai/artifact/JYEWEZyCDC1yXwjtYhPzCC) ·
full design: [ARCHITECTURE.md](ARCHITECTURE.md)

```mermaid
flowchart LR
  subgraph IN["People & inputs"]
    P["👥 People<br/>analyst · team lead<br/>fraud manager · regulator"]
    MC["🔌 MCP client"]
    AF["📄 Alert feed<br/>case pack, risk model"]
  end

  subgraph BANK["Bank boundary"]
    direction LR
    subgraph FRONT["Front door :3000"]
      SI["🔒 Sign-in & roles<br/>one session for all"]
      CO["🖥️ Console<br/>queue · timeline · graph"]
      RV["👁️ Regulator view<br/>read-only · verifies chain"]
      MT["🔌 MCP tools<br/>5 read-only"]
      RU["▶️ Runner · run.sh<br/>case · pack · grade"]
      ASK["💬 Ask Sentinel — NEW<br/>role-scoped GraphRAG Q&A<br/>cited · 3 guardrails"]
    end
    subgraph DL["Decision layer · sentinel.typologies"]
      VA["✔ Validate alert"] --> BR["▤ Batched graph read"] --> DT["◎ 8 detectors + rings"] --> HC["🕒 History checks"]
      HC --> RL["◆ Rules<br/>typology · R2 · R1 · R7"]
      RL -- tricky only --> MG["▣ Model gate"]
      RL --> PE["📋 Policy engine"]
      MG --> PE --> CP["🛡 Containment"]
      CP -- L1/L2 requests --> AP["🖊 Approval chain"] --> WB["⟲ Write-back & read-back"]
    end
    subgraph ST["Bank-side stores"]
      UP["Your uploads<br/>private per person"]
      LG["⛓ Audit ledger<br/>hash-chained"]
      AFL["Answer files"]
      US["🔑 User store"]
      MI["Lab mirror<br/>backtests only"]
    end
  end

  subgraph OUT["Outside the bank"]
    TG[("TigerGraph Savanna 4.x<br/>GRAPH_GOA · 36 queries<br/>ClosedCase vectors<br/>+ 3 onboarded bank graphs")]
    OA["☁ OpenAI API<br/>decisions · embeddings<br/>Ask Sentinel answers"]
    OB["🧩 Bank onboarding — NEW<br/>any schema → canonical model<br/>propose · review · approve"]
  end

  P -- username + password --> SI --> CO
  MC -- tool call --> MT --> BR
  AF -- alert row --> RU --> VA
  CO --> VA
  BR -- GSQL reads --> TG
  MG -- evidence bundle --> OA
  WB -- FraudCase + edges, read back --> TG
  WB -- every step --> LG
  WB --> AFL
  P -. logo button, every page .-> ASK
  ASK -. allowed sources → cited answer .-> OA
  ASK -. graph facts · vectors .-> TG
  ASK -. every question .-> LG
  ASK -. reads .-> UP
  OB -. approved mapping .-> BR
```

| Layer | What it holds |
|---|---|
| **People & inputs** | four roles; an MCP-speaking assistant; the alert feed |
| **Front door :3000** | one sign-in and session for every service; the console; the regulator view; read-only MCP tools; the runner; Ask Sentinel |
| **Decision layer** | validate → one batched graph read → 8 detectors → history checks → rules → model (tricky cases only) → policy engine → containment → human approval → write-back with read-back |
| **Bank-side stores** | private uploads, the hash-chained audit ledger, answer files, salted user store, the lab mirror (backtests only, never a silent fallback) |
| **Outside the bank** | TigerGraph (the source of truth, plus each onboarded bank's own graph), OpenAI (pseudonymous ids only), bank onboarding |

**The numbers:** 6 → 16/20 Round 1 cases correct live · 4 → 0 innocent customers blocked · 2.0% held-out false
positives · 894 hash-chained audit entries · 3 assistant guardrails, 0 withheld sources sent to the model ·
3 bank schemas onboarded (ISO 20022, BIAN, T24) with the same detectors.
