/**
 * GENERATED — do not hand-edit.
 *
 * Emitted by:
 *   python -m api.contract emit --out frontend/src/lib/generated/contract.ts
 *
 * CI runs the same command with --check, which fails when this file and
 * the backend disagree.
 *
 * The closed vocabularies exist once, in `backend/sentinel/domain/enums.py`;
 * the response shapes exist once, in `backend/api/schemas.py`. Hand-typing
 * "BLOCK_ALL_CARDS" anywhere in this app is how the third copy drifts.
 * See HLD ADR-7.
 */

// ── Enums ───────────────────────────────────────────────────────────────────

/** The 14 policy actions. This is the complete set — nothing else is an action. */
export const ACTIONS = [
  "ALLOW_TRANSACTION",
  "DECLINE_TRANSACTION",
  "MONITOR_CARD",
  "MONITOR_CONNECTED_CARDS",
  "WARN_CUSTOMER",
  "VERIFY_WITH_CUSTOMER",
  "STEP_UP_AUTH",
  "BLOCK_CARD",
  "BLOCK_ALL_CARDS",
  "GENERATE_REPORT",
  "CREATE_CASE",
  "FILE_REPORT",
  "ESCALATE_TO_ANALYST",
  "CLOSE_NO_FRAUD",
] as const;
export type Action = (typeof ACTIONS)[number];

/** Approval routes. `auto` is the only one the agent may execute alone. */
export const ROUTES = [
  "auto",
  "L1",
  "L2",
] as const;
export type Route = (typeof ROUTES)[number];

/** Who is acting. Authorization is real; authentication is demo-grade. */
export const ROLES = [
  "analyst",
  "team_lead",
  "fraud_manager",
] as const;
export type Role = (typeof ROLES)[number];

/** `uncertain` is a valid verdict and earns full credit on ambiguous cases. */
export const VERDICTS = [
  "fraud",
  "legitimate",
  "uncertain",
] as const;
export type Verdict = (typeof VERDICTS)[number];

export const CASE_STATUSES = [
  "open",
  "closed_fraud",
  "closed_legitimate",
  "escalated",
] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

/**
 * The five documented patterns, plus `undocumented` and `none`.
 *
 * `undocumented` requires a non-empty `pattern_description`; finding one is
 * explicitly scored.
 */
export const PATTERNS = [
  "card_testing",
  "card_not_present_fraud",
  "card_not_present_new_device",
  "out_of_region_use",
  "account_takeover",
  "undocumented",
  "none",
] as const;
export type Pattern = (typeof PATTERNS)[number];

/** `DOCUMENT` is GraphRAG's visible footprint in the deliverable. */
export const EVIDENCE_SOURCES = [
  "graph",
  "document",
  "customer",
  "external",
] as const;
export type EvidenceSource = (typeof EVIDENCE_SOURCES)[number];

/** The three evidence requests the agent may make without approval. */
export const REQUEST_TYPES = [
  "customer_validation",
  "step_up_auth",
  "analyst_info",
] as const;
export type RequestType = (typeof REQUEST_TYPES)[number];

/** How an alert arrived. The benchmark is 11 / 8 / 1 in this order. */
export const TRIGGER_TYPES = [
  "risk_score",
  "customer_report",
  "analyst_request",
] as const;
export type TriggerType = (typeof TRIGGER_TYPES)[number];

/**
 * The outcome of a requested evidence round.
 *
 * `NO_REPLY` is the honest outcome for a genuinely ambiguous case and routes
 * into R4 — it is not a failure to simulate.
 */
export const CUSTOMER_RESPONSES = [
  "denied",
  "confirmed",
  "no_reply",
  "step_up_passed",
  "step_up_failed",
] as const;
export type CustomerResponse = (typeof CUSTOMER_RESPONSES)[number];

/**
 * The ten investigation steps, named by the step classes themselves.
 *
 * Read off `sentinel.agents.steps` rather than transcribed, because these
 * names reach the UI timeline and the persisted trace — a rename there with
 * no rename here is a silent gap in both.
 */
export const STEP_NAMES = [
  "scope",
  "plan",
  "sweep",
  "recall",
  "assess",
  "stop_test",
  "request_evidence",
  "decide",
  "narrate",
  "write",
] as const;
export type StepName = (typeof STEP_NAMES)[number];

/** Where one investigation is. `cancelled` is distinct from `failed`. */
export const RUN_STATUSES = [
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/**
 * How a run produces its events.
 *
 * `replay` re-reads a journal at demo speed and `scripted` plays a
 * fixture; both exist so the console can be shown without credentials, and
 * both are recorded on the run so a trace can never be mistaken for a live
 * investigation.
 */
export const RUN_MODES = [
  "live",
  "replay",
  "scripted",
] as const;
export type RunMode = (typeof RUN_MODES)[number];

/** Which of the answer file's two recommendation lists an action came from. */
export const PHASES = [
  "initial",
  "final",
] as const;
export type Phase = (typeof PHASES)[number];

/** What one execution attempt did. Denials and failures are rows, not absences. */
export const EXECUTION_OUTCOMES = [
  "executed",
  "denied",
  "failed",
  "replayed",
] as const;
export type ExecutionOutcome = (typeof EXECUTION_OUTCOMES)[number];

/** Where one approval is. Only `pending` is unique per case, action and phase. */
export const APPROVAL_STATUSES = [
  "pending",
  "approved",
  "rejected",
] as const;
export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];

/** The verb in `POST /api/approvals/{id}/decision`. */
export const APPROVAL_DECISIONS = [
  "approve",
  "reject",
] as const;
export type ApprovalDecision = (typeof APPROVAL_DECISIONS)[number];

/**
 * What `POST /api/actions/execute` does when the route needs an approval.
 *
 * `enqueue` is the default because the exit test is one click from blocked
 * to inbox: the 403 carries the approval it just created. `reject` opts
 * out, for a caller that wants the refusal and nothing else.
 */
export const DENIAL_POLICIES = [
  "enqueue",
  "reject",
] as const;
export type DenialPolicy = (typeof DENIAL_POLICIES)[number];

/**
 * Queue orderings. `opened_at` is the default and the run order.
 *
 * Sorting by `case_id` is *not* chronological — the pack's ids and its
 * `opened_at` values disagree, and case memory only compounds forwards.
 */
export const CASE_SORTS = [
  "opened_at",
  "case_id",
  "probability",
  "exposure",
] as const;
export type CaseSort = (typeof CASE_SORTS)[number];

/**
 * The order a benchmark batch runs its cases in.
 *
 * `chronological` is the only defensible default: HHG-019 may retrieve the
 * `FraudCase` Sentinel wrote for HHG-003, and must not be able to retrieve
 * one from a case that had not happened yet.
 */
export const BATCH_ORDERS = [
  "chronological",
  "case_id",
  "as_listed",
] as const;
export type BatchOrder = (typeof BATCH_ORDERS)[number];

/** Liveness. `degraded` is the honest answer while the graph workspace wakes. */
export const SERVICE_STATUSES = [
  "ok",
  "degraded",
  "down",
] as const;
export type ServiceStatus = (typeof SERVICE_STATUSES)[number];

/**
 * What a retrieved item is. The three kinds `GraphRagRetriever` emits.
 *
 * The values come from the retriever's own constants rather than being
 * retyped: `sentinel_case` is Sentinel's own memory and `closed_case` is
 * one of the bank's 5,565, and the console renders them differently.
 */
export const RETRIEVAL_KINDS = [
  "closed_case",
  "policy_doc",
  "sentinel_case",
] as const;
export type RetrievalKind = (typeof RETRIEVAL_KINDS)[number];

/**
 * The vertex types `graph/schema.gsql` defines, verbatim.
 *
 * The canvas encodes each kind differently — a `FraudCase` is Sentinel's
 * own memory and is drawn in the accent colour, a `ClosedCase` is the
 * bank's and is not — so the kind is a closed vocabulary, not a label.
 */
export const GRAPH_NODE_KINDS = [
  "Customer",
  "Card",
  "Transaction",
  "DeviceProfile",
  "BillingRegion",
  "EmailDomain",
  "ProductCode",
  "ClosedCase",
  "FraudCase",
  "Alert",
  "PolicyDoc",
] as const;
export type GraphNodeKind = (typeof GRAPH_NODE_KINDS)[number];

// ── The policy: routes, approvers, thresholds ───────────────────────────────

/** The auto/L1/L2 table. BLOCK_CARD is absent because it is the only
 *  exposure-dependent action in the policy: L1 at exposure <= the L2
 *  threshold (inclusive), L2 above it. */
export const FIXED_ROUTES: Partial<Record<Action, Route>> = {
  ALLOW_TRANSACTION: "auto",
  DECLINE_TRANSACTION: "L1",
  MONITOR_CARD: "auto",
  MONITOR_CONNECTED_CARDS: "auto",
  WARN_CUSTOMER: "auto",
  VERIFY_WITH_CUSTOMER: "auto",
  STEP_UP_AUTH: "auto",
  BLOCK_ALL_CARDS: "L2",
  GENERATE_REPORT: "auto",
  CREATE_CASE: "auto",
  FILE_REPORT: "L2",
  ESCALATE_TO_ANALYST: "auto",
  CLOSE_NO_FRAUD: "auto",
};

/** Actions that are unconditionally `auto`. Ten of the fourteen. */
export const AUTO_ACTIONS: Action[] = [
  "ALLOW_TRANSACTION",
  "MONITOR_CARD",
  "MONITOR_CONNECTED_CARDS",
  "WARN_CUSTOMER",
  "VERIFY_WITH_CUSTOMER",
  "STEP_UP_AUTH",
  "GENERATE_REPORT",
  "CREATE_CASE",
  "ESCALATE_TO_ANALYST",
  "CLOSE_NO_FRAUD",
];

/** Which roles may approve which route. */
export const APPROVERS: Record<Route, Role[]> = {
  auto: ["analyst", "team_lead", "fraud_manager"],
  L1: ["team_lead", "fraud_manager"],
  L2: ["fraud_manager"],
};

/** Every tunable number in the Fraud Policy, from PolicyConfig. */
export const BLOCK_CARD_L2_THRESHOLD = 2500.0;
export const SAR_EXPOSURE_THRESHOLD = 1000.0;
export const CASE_PROBABILITY_THRESHOLD = 0.3;
export const VERIFY_BEFORE_BLOCK_THRESHOLD = 0.7;
export const ESCALATE_EXPOSURE_THRESHOLD = 500.0;
export const R4_ESCALATE_THRESHOLD = 500.0;
export const STOP_HIGH = 0.85;
export const STOP_LOW = 0.15;
export const MIN_INDEPENDENT_SUPPORT = 2;

/** Total log-odds any one evidence group may contribute, in either direction. */
export const GROUP_CAP = 1.2;

/** The installed graph queries. A tool name in a ref is one of these. */
export const TOOL_NAMES = [
  "txn_detail",
  "card_baseline",
  "card_window",
  "card_testing_probe",
  "region_novelty",
  "amount_band_probe",
  "device_novelty",
  "device_neighbors",
  "region_cluster",
  "email_cluster",
  "ring_expand",
  "recurring_charge_probe",
  "velocity_probe",
  "customer_case_history",
  "similar_prior_cases",
  "case_memory_for_card",
  "txn_sequence_context",
  "product_novelty",
  "card_amount_stats",
] as const;
export type ToolName = (typeof TOOL_NAMES)[number];

/** Display only. The route that binds is always the one the server recomputed. */
export function routeFor(action: Action, exposureUsd: number): Route {
  const fixed = FIXED_ROUTES[action];
  if (fixed) return fixed;
  if (action === "BLOCK_CARD")
    return exposureUsd <= BLOCK_CARD_L2_THRESHOLD ? "L1" : "L2";
  throw new Error(`no route defined for ${action}`);
}

// ── The answer contract ─────────────────────────────────────────────────────

/**
 * One claim, where it came from, what backs it, and the ids it rests on.
 *
 * Responsibility: the atomic unit of the graded evidence list. Exactly four
 * fields — the assembler may not attach scores, confidences or timestamps here,
 * because anything beyond these four is an extra key in a scored file.
 * Collaborators: `EvidenceRef` produces `ref`; `GraphIdentityChecker`
 * reads `entity_ids` and proves each one exists.
 */
export interface Evidence {
  claim: string;
  source: EvidenceSource;
  ref: string;
  entity_ids: string[];
}

/**
 * One round of evidence the agent asked for and then assumed the answer to.
 *
 * Responsibility: record that the agent stopped and asked, at which step, and
 * what it assumed came back — Guide.md supplies no customer replies, so the
 * stated assumption is the only thing that makes `final` defensible.
 * Collaborators: `EvidenceSimulator` fills `assumed_response` from graph
 * facts; `StepCounter` supplies `asked_after_step`.
 */
export interface EvidenceRequest {
  type: RequestType;
  asked_after_step: number;
  assumed_response: string;
}

/**
 * One next best action with its approval route and the rule that justifies it.
 *
 * Responsibility: carry the three graded fields and nothing else. Whether the
 * route is *correct* for the action is `RoutingRule`'s job in the validation
 * package — it needs `exposure_usd`, which is on the case, not here.
 * Collaborators: produced by `PolicyEngine`; re-checked by `RoutingRule`.
 */
export interface ActionRecommendation {
  action: Action;
  route: Route;
  reason: string;
}

/**
 * What the agent recommended before the requested evidence, and after it.
 *
 * Responsibility: hold both phases plus the one-line account of the difference.
 * Collaborators: `PolicyEngine` runs twice, once per `CaseState`;
 * `AnswerFile.final_equals_initial_without_requests` ties the two phases to
 * whether anything was actually asked.
 */
export interface NextBestActions {
  initial: ActionRecommendation[];
  final: ActionRecommendation[];
  what_changed: string;
}

/**
 * The regulatory filing, or the reasoned decision not to file one.
 *
 * Responsibility: enforce that the report is all-or-nothing. A half-filled SAR
 * is worse than none: it reads as a filing to the grader and to a regulator
 * while missing the fields either of them would act on.
 * Collaborators: `SarPolicy` decides `file` and writes `reason`;
 * `NarrationAgent` writes `narrative`; `EpisodeScoper` supplies
 * `total_amount_usd` and the two activity dates.
 */
export interface SarReport {
  file: boolean;
  reason: string;
  narrative: string;
  subjects: string[];
  total_amount_usd: number;
  activity_dates: string[];
}

/**
 * The bank's internal record of the investigation — Part 1 of the answer.
 *
 * Responsibility: the fifteen graded case fields and the five invariants that
 * need nothing outside the case itself.
 * Collaborators: `EpisodeScoper` fills the episode fields and
 * `exposure_usd`; `EvidenceLedger` fills `fraud_probability`;
 * `CaseMemoryStore` fills `written_to_graph` and `graph_case_id`.
 */
export interface Case {
  status: CaseStatus;
  verdict: Verdict;
  fraud_probability: number;
  pattern: Pattern;
  pattern_description: string;
  affected_txn_ids: string[];
  first_suspicious_txn_id: string;
  connected_card_ids: string[];
  /** "DeviceInfo | OS | browser | screen" — the label form, not a vertex key. */
  connected_device_profiles: string[];
  exposure_usd: number;
  evidence: Evidence[];
  similar_prior_cases: string[];
  summary: string;
  written_to_graph: boolean;
  graph_case_id: string;
}

/**
 * One `cases/<case_id>.json` file, whole.
 *
 * Responsibility: the nine top-level fields, and the three invariants that span
 * more than one part of the answer — the SAR against the final actions, the
 * legitimate verdict against the episode and the SAR, and the two action phases
 * against whether any evidence was actually requested.
 * Collaborators: `AnswerAssembler` constructs it; `AnswerValidator` and
 * `GraphIdentityChecker` check what these models structurally cannot.
 */
export interface AnswerFile {
  case_id: string;
  case: Case;
  evidence_requests: EvidenceRequest[];
  next_best_actions: NextBestActions;
  sar: SarReport;
  stop_reason: string;
  tool_calls: number;
  tokens: number;
  latency_s: number;
}

/**
 * One row of the case pack: the work item an investigation starts from.
 *
 * Responsibility: hold the nine alert fields with the two dataset traps already
 * neutralised — the missing-score sentinel parsed to `None`, and
 * `opened_at` kept distinct from the transaction timestamp.
 * Collaborators: `CasePackLoader` builds it; `InvestigationContext`
 * carries it through every step; `TriggerContext` derives from it.
 */
export interface Alert {
  alert_id: string;
  /** When the alert was raised — one to six hours after the flagged transaction, never zero. Anchor a window on the transaction, not on this. */
  opened_at: string;
  trigger_type: TriggerType;
  trigger_text: string;
  flagged_txn_id: string;
  card_id: string;
  customer_id: string;
  /** The score quoted in the alert, or null on the nine that quoted none. Never a risk-band input; the model's score is on the transaction. */
  risk_score: number | null;
  status: string;
}

// ── Shared value objects ────────────────────────────────────────────────────

/**
 * The ceilings for one run and what it has spent, as the budget bar reads it.
 *
 * Responsibility: mirror `sentinel.agents.budget.BudgetSnapshot` field for
 * field so `model_validate(budget.snapshot())` is the whole conversion.
 * Collaborators: the `budget.updated` event, `run.started`'s payload and
 * `RunDetail`.
 */
export interface BudgetSnapshot {
  tool_calls: number;
  max_tool_calls: number;
  evidence_rounds: number;
  max_evidence_rounds: number;
  tokens: number;
  max_tokens: number;
  usd: number;
  max_usd: number;
  elapsed_s: number;
  max_elapsed_s: number;
}

/**
 * One defect in one answer file, addressed by its JSON path.
 *
 * Mirrors `sentinel.validation.validator.Finding`: a stable `code` and a
 * path, because "invalid submission" alone tells an analyst nothing about
 * which of the 47 fields to fix.
 */
export interface ValidationFinding {
  code: string;
  path: string;
  message: string;
}

/**
 * One retrieved item, with the evidence of how it was found.
 *
 * Mirrors `sentinel.rag.retriever.RetrievalHit`. `provenance` is the edge
 * the expansion actually traversed — it is what makes the memory tab show
 * graph retrieval rather than a vector store standing next to a graph.
 * Every field but `id` carries a default because the structural-only path
 * emits ids alone.
 */
export interface RetrievalHit {
  id: string;
  kind: RetrievalKind;
  score: number;
  title: string;
  snippet: string;
  ref: string;
  provenance: Record<string, unknown>;
}

/**
 * What one policy gate did, in the words the UI renders.
 *
 * Mirrors `sentinel.policy.gates.base.GateOutcome`. The analyst's question
 * is never "did a gate run" but "which bar stopped the block", so the removed
 * and added actions are named rather than counted.
 */
export interface GateOutcome {
  gate: string;
  removed: Action[];
  added: Action[];
  reason: string;
}

/**
 * `sar.file` and its reason, as the policy evaluation carries them.
 *
 * Both fields travel together because they are one decision: the reason is a
 * graded field on the answer and is populated in the not-filing branch too.
 */
export interface SarSummary {
  file: boolean;
  reason: string;
}

/** Policy section 6's answer at one moment, with the sentence behind it. */
export interface StopSummary {
  should_stop: boolean;
  reason: string;
}

// ── The SSE stream ──────────────────────────────────────────────────────────

export type SentinelEventType =
  | "run.started"
  | "step.started"
  | "step.completed"
  | "tool.called"
  | "evidence.posted"
  | "retrieval.completed"
  | "pattern.rejected"
  | "policy.evaluated"
  | "evidence.requested"
  | "llm.completed"
  | "budget.updated"
  | "verdict.reached"
  | "case.written"
  | "case.write_failed"
  | "validation.completed"
  | "run.completed"
  | "run.failed";

export const SENTINEL_EVENT_TYPES: SentinelEventType[] = [
  "run.started",
  "step.started",
  "step.completed",
  "tool.called",
  "evidence.posted",
  "retrieval.completed",
  "pattern.rejected",
  "policy.evaluated",
  "evidence.requested",
  "llm.completed",
  "budget.updated",
  "verdict.reached",
  "case.written",
  "case.write_failed",
  "validation.completed",
  "run.completed",
  "run.failed",
];

/** After one of these the server closes the connection; do not reconnect. */
export const TERMINAL_EVENTS: SentinelEventType[] = ["run.completed", "run.failed"];

export interface SseEnvelope<T extends SentinelEventType, P> {
  v: 1;
  /** == the SSE `id:` field. Monotonic per run from 1, so replay is `seq > n`. */
  seq: number;
  run_id: string;
  case_id: string;
  type: T;
  at: string;
  /** The investigation step. The SAME counter that fills
   *  answer.evidence_requests[].asked_after_step. Null on run-level events. */
  step: number | null;
  payload: P;
}

/** The alert, the prior it bought and the ceilings the run starts under. */
export interface RunStartedPayload {
  case_id: string;
  trigger_type: TriggerType;
  trigger_text: string;
  flagged_txn_id: string;
  card_id: string;
  customer_id: string;
  /** Null on nine of the twenty: the alert carried the -1 missing-numeric sentinel, not a score. The model's score is on the transaction. */
  alert_risk_score: number | null;
  /** Where the probability starts — the trigger type's fitted prior. */
  prior: number;
  mode: RunMode;
  orchestrator: string;
  budget: BudgetSnapshot;
}

/** A numbered stage began. `agent` is the class that runs it. */
export interface StepStartedPayload {
  step: number;
  name: StepName;
  title: string;
  agent: string;
}

/** A stage ended, with what it cost and what it found. */
export interface StepCompletedPayload {
  step: number;
  name: StepName;
  elapsed_s: number;
  tool_calls_in_step: number;
  postings_in_step: number;
}

/**
 * One graph query, with the citation it produced.
 *
 * `ref` is the string that lands in `answer.case.evidence[].ref`, so the
 * timeline row and the graded evidence item are provably the same call.
 */
export interface ToolCalledPayload {
  step: number;
  tool: string;
  ref: string;
  params: Record<string, string | number | boolean>;
  entity_ids: string[];
  elapsed_s: number;
  ok: boolean;
  error: string | null;
  summary: string;
}

/**
 * One finding priced as a likelihood ratio, and the probability either side.
 *
 * The same thirteen fields as `PostingView` minus the ones only a
 * stored trace has — this is the live form, and the console's evidence list
 * is built from it before any answer file exists.
 */
export interface EvidencePostedPayload {
  step: number;
  feature: string;
  /** False means the feature's absence likelihood was posted. Absence is evidence. */
  present: boolean;
  group: string;
  claim: string;
  source: EvidenceSource;
  ref: string;
  entity_ids: string[];
  lr: number;
  log_lr: number;
  p_before: number;
  p_after: number;
  /** The group cap clipped this posting — the UI draws a hatched bar end. */
  capped: boolean;
}

/** What one retrieval asked for and what came back. */
export interface RetrievalCompletedPayload {
  step: number;
  kind: RetrievalKind;
  /** Free text, not a closed set: the recall step emits "structural" for the card lookup and "hybrid (vector + structural, RRF)" for the fused one. */
  strategy: string;
  query: string;
  hits: RetrievalHit[];
}

/**
 * A pattern the model named and the measurements ruled out.
 *
 * Emitted rather than swallowed. The pattern is a graded field and it feeds
 * the episode scoper, so a rejection is a thing an analyst should be able to
 * see in the timeline — "it said out-of-region use; the card has 42 prior
 * transactions in that region" — rather than a value that quietly became
 * `none`.
 */
export interface PatternRejectedPayload {
  step: number;
  named: Pattern;
  contradiction: string;
  accepted: Pattern;
}

/**
 * One pass of the policy engine over one state.
 *
 * Emitted twice per case — once per phase — and the two payloads side by side
 * are the initial-to-final diff the console renders.
 */
export interface PolicyEvaluatedPayload {
  step: number;
  phase: Phase;
  /** The state's probability, not the ledger's. On the initial phase they differ: the state holds the snapshot taken before the requested evidence. */
  fraud_probability: number;
  exposure_usd: number;
  verdict: Verdict;
  /** Distinct ledger groups that moved by more than 0.05. Policy 6 counts these. */
  independent_support: number;
  recommendations: ActionRecommendation[];
  gates_applied: GateOutcome[];
  sar: SarSummary;
  stop: StopSummary;
}

/** The one round of evidence the agent may ask for, and what it assumed back. */
export interface EvidenceRequestedPayload {
  /** The same counter that fills answer.evidence_requests[].asked_after_step. */
  step: number;
  request_index: number;
  type: RequestType;
  rationale: string;
  assumed_response: string;
  /** The query refs the assumption rests on — never a guess. */
  assumption_basis: string[];
  branch: CustomerResponse;
  log_lr_applied: number;
}

/**
 * One model call, its tokens and its cost.
 *
 * `fallback` is true when the call failed and the agent degraded to its
 * deterministic output: the trace has to show that the step ran without a
 * model rather than quietly looking the same.
 */
export interface LlmCompletedPayload {
  step: number;
  purpose: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  usd: number;
  elapsed_s: number;
  fallback: boolean;
  error: string;
}

/** The whole case shape, plus the trajectory that got there. */
export interface VerdictReachedPayload {
  step: number | null;
  status: CaseStatus;
  verdict: Verdict;
  fraud_probability: number;
  pattern: Pattern;
  pattern_description: string;
  exposure_usd: number;
  affected_txn_ids: string[];
  connected_card_ids: string[];
  connected_device_profiles: string[];
  similar_prior_cases: string[];
  summary: string;
  stop_reason: string;
  /** The prior followed by the probability after each posting. */
  trajectory: number[];
}

/** The case vertex and its edges landed in the graph — Sentinel's own memory. */
export interface CaseWrittenPayload {
  step: number | null;
  graph_case_id: string;
  vertices_upserted: number;
  edges_upserted: number;
  edge_types: string[];
  embedded: boolean;
  answer_path: string;
}

/**
 * The write-back failed and the run continued.
 *
 * A separate event rather than a `run.failed`: the answer file is the
 * submission and it is already valid, so losing the memory write is a warning
 * on the case, not the end of the investigation.
 */
export interface CaseWriteFailedPayload {
  step: number | null;
  case_id: string;
  code: string;
  message: string;
}

/** The answer was validated. `graph_checked` says whether ids were proven. */
export interface ValidationCompletedPayload {
  step: number | null;
  ok: boolean;
  errors: ValidationFinding[];
  warnings: ValidationFinding[];
  graph_checked: boolean;
}

/** Terminal. The connection closes after this one. */
export interface RunCompletedPayload {
  status: RunStatus;
  tool_calls: number;
  /** Never 0 on an agent run. */
  tokens: number;
  latency_s: number;
  validation_ok: boolean;
  answer_url: string;
}

/**
 * Terminal. `code` is the domain error's own code, not an HTTP status.
 *
 * Values seen in practice: `budget_exceeded`, `answer_invalid`,
 * `graph_unavailable`, `llm_unavailable`, `vertex_not_found`,
 * `query_failed` — any `SentinelError.code`, so this is a string rather
 * than a closed union that would go stale the first time one is added.
 */
export interface RunFailedPayload {
  code: string;
  message: string;
  step: number | null;
  recoverable: boolean;
}

/** The payload that belongs to each event name. */
export interface EventPayloadMap {
  "run.started": RunStartedPayload;
  "step.started": StepStartedPayload;
  "step.completed": StepCompletedPayload;
  "tool.called": ToolCalledPayload;
  "evidence.posted": EvidencePostedPayload;
  "retrieval.completed": RetrievalCompletedPayload;
  "pattern.rejected": PatternRejectedPayload;
  "policy.evaluated": PolicyEvaluatedPayload;
  "evidence.requested": EvidenceRequestedPayload;
  "llm.completed": LlmCompletedPayload;
  "budget.updated": BudgetSnapshot;
  "verdict.reached": VerdictReachedPayload;
  "case.written": CaseWrittenPayload;
  "case.write_failed": CaseWriteFailedPayload;
  "validation.completed": ValidationCompletedPayload;
  "run.completed": RunCompletedPayload;
  "run.failed": RunFailedPayload;
}

export type SentinelEvent =
  | SseEnvelope<"run.started", RunStartedPayload>
  | SseEnvelope<"step.started", StepStartedPayload>
  | SseEnvelope<"step.completed", StepCompletedPayload>
  | SseEnvelope<"tool.called", ToolCalledPayload>
  | SseEnvelope<"evidence.posted", EvidencePostedPayload>
  | SseEnvelope<"retrieval.completed", RetrievalCompletedPayload>
  | SseEnvelope<"pattern.rejected", PatternRejectedPayload>
  | SseEnvelope<"policy.evaluated", PolicyEvaluatedPayload>
  | SseEnvelope<"evidence.requested", EvidenceRequestedPayload>
  | SseEnvelope<"llm.completed", LlmCompletedPayload>
  | SseEnvelope<"budget.updated", BudgetSnapshot>
  | SseEnvelope<"verdict.reached", VerdictReachedPayload>
  | SseEnvelope<"case.written", CaseWrittenPayload>
  | SseEnvelope<"case.write_failed", CaseWriteFailedPayload>
  | SseEnvelope<"validation.completed", ValidationCompletedPayload>
  | SseEnvelope<"run.completed", RunCompletedPayload>
  | SseEnvelope<"run.failed", RunFailedPayload>;

export const isEvent =
  <T extends SentinelEventType>(t: T) =>
  (e: SentinelEvent): e is Extract<SentinelEvent, { type: T }> =>
    e.type === t;

// ── API responses ───────────────────────────────────────────────────────────

/** `GET /api/health` — the process is up. No dependency is touched. */
export interface Health {
  status: ServiceStatus;
  version: string;
  uptime_s: number;
}

/** One dependency, probed. `detail` carries the failure, not a log line. */
export interface ReadyCheck {
  name: string;
  ok: boolean;
  detail: string;
  elapsed_ms: number;
}

/**
 * `GET /api/ready` — 503 when any check fails.
 *
 * Separate from health because the TigerGraph workspace auto-stops and takes
 * about 45 s to come back: the process is fine, the system is not, and a
 * load balancer must be able to tell those apart.
 */
export interface Ready {
  ok: boolean;
  checks: ReadyCheck[];
}

/**
 * Every closed vocabulary the console renders, served from the domain.
 *
 * Responsibility: let a client populate a filter without hard-coding a list
 * that would become the third copy. HLD ADR-7.
 */
export interface MetaEnums {
  actions: Action[];
  routes: Route[];
  roles: Role[];
  verdicts: Verdict[];
  case_statuses: CaseStatus[];
  patterns: Pattern[];
  evidence_sources: EvidenceSource[];
  request_types: RequestType[];
  trigger_types: TriggerType[];
  customer_responses: CustomerResponse[];
  run_statuses: RunStatus[];
  execution_outcomes: ExecutionOutcome[];
  approval_statuses: ApprovalStatus[];
  phases: Phase[];
}

/**
 * The approval table, served rather than reimplemented in the client.
 *
 * `fixed` holds the thirteen exposure-independent actions. `BLOCK_CARD`
 * is deliberately absent: it is the only action whose route depends on
 * exposure, and a client that found it here would stop asking the server.
 */
export interface MetaRouting {
  fixed: Partial<Record<Action, Route>>;
  approvers: Partial<Record<Route, Role[]>>;
  block_card_l2_threshold: number;
}

/** Every tunable number in the Fraud Policy, so the UI can show the rule's own figure. */
export interface MetaThresholds {
  block_card_l2_threshold: number;
  sar_exposure_threshold: number;
  case_probability_threshold: number;
  verify_before_block_threshold: number;
  escalate_exposure_threshold: number;
  r4_escalate_threshold: number;
  stop_high: number;
  stop_low: number;
  min_independent_support: number;
  /** Total log-odds any one evidence group may contribute, either way. */
  group_cap: number;
}

/**
 * `GET /api/meta` — the whole contract, from the objects that own it.
 *
 * Responsibility: be the runtime half of ADR-7. The generated TypeScript is
 * the build-time half; this endpoint is what proves the running server agrees
 * with it.
 * Collaborators: `sentinel.domain.enums`, `PolicyConfig`, the tool
 * catalogue and the event vocabulary — each read, none restated.
 */
export interface Meta {
  version: string;
  enums: MetaEnums;
  routing: MetaRouting;
  thresholds: MetaThresholds;
  tools: string[];
  events: string[];
  terminal_events: string[];
  steps: StepName[];
}

/**
 * One row of the queue — an alert, plus whatever has been learned about it.
 *
 * Responsibility: render for all twenty alerts from the first request, which
 * is why every answer-derived field is optional and defaulted. Nineteen of
 * the twenty have no answer for most of a benchmark run, and a queue that
 * 404s or 500s on them is a queue that cannot be demonstrated. FR-27.
 * Collaborators: `CasePackLoader` supplies the alert half; the answer file
 * and the `run` row supply the rest, through `of`.
 */
export interface CaseSummary {
  case_id: string;
  opened_at: string;
  trigger_type: TriggerType;
  trigger_text: string;
  flagged_txn_id: string;
  card_id: string;
  customer_id: string;
  /** Null on nine of the twenty — the alert carried no score, not a zero. */
  alert_risk_score: number | null;
  alert_status: string;
  has_answer: boolean;
  status: CaseStatus | null;
  verdict: Verdict | null;
  fraud_probability: number | null;
  pattern: Pattern | null;
  exposure_usd: number | null;
  affected_txn_ids: string[];
  connected_card_ids: string[];
  similar_prior_cases: string[];
  summary: string;
  sar_filed: boolean | null;
  final_actions: Action[];
  written_to_graph: boolean;
  graph_case_id: string;
  run_id: string | null;
  run_status: RunStatus | null;
}

/**
 * `GET /api/cases/{case_id}/validation` — what one validation pass found.
 *
 * Responsibility: carry the report and say whether the graph half ran.
 * `graph_checked` is false for the pure pass, which is the one the
 * orchestrator runs before writing and the one CI runs with no credentials.
 * Collaborators: `AnswerValidator` and `GraphIdentityChecker`.
 */
export interface ValidationBody {
  ok: boolean;
  errors: ValidationFinding[];
  warnings: ValidationFinding[];
  graph_checked: boolean;
}

/**
 * One evidence posting and what it did to the probability.
 *
 * Mirrors `sentinel.evidence.ledger.Posting` — including `group` and
 * `capped`, which are what let the trace show *why* the third phrasing of a
 * device finding moved the number by nothing.
 */
export interface PostingView {
  feature: string;
  present: boolean;
  lr: number;
  log_lr: number;
  p_before: number;
  p_after: number;
  claim: string;
  ref: string;
  source: EvidenceSource;
  entity_ids: string[];
  capped: boolean;
  group: string;
  /** The step this was posted in. Null when replayed from a trace file. */
  step: number | null;
}

/**
 * One stage of a finished investigation, with the calls it made.
 *
 * `calls` holds the `tool.called` payloads verbatim rather than a
 * reduction of them, because the ref on each call is the citation the graded
 * evidence item carries and the trace is where an analyst checks that.
 */
export interface TraceStep {
  step: number;
  name: StepName;
  title: string;
  agent: string;
  elapsed_s: number;
  tool_calls_in_step: number;
  postings_in_step: number;
  completed: boolean;
  calls: ToolCalledPayload[];
}

/**
 * `GET /api/cases/{case_id}/trace` — how the answer was arrived at.
 *
 * Responsibility: the three things the answer format has nowhere to put — the
 * step trace, the postings and the probability trajectory — beside the three
 * counters it does. ADR-8: the answer file stays the only submission
 * artefact, and this is where everything the UI needs lives instead.
 * Collaborators: the event journal, or the trace file a CLI run wrote.
 */
export interface TraceBody {
  case_id: string;
  run_id: string;
  steps: TraceStep[];
  postings: PostingView[];
  trajectory: number[];
  tool_calls: number;
  tokens: number;
  latency_s: number;
}

/**
 * What the write-back actually put in the graph.
 *
 * `embedded` matters on its own: a case vertex written without its
 * embedding is invisible to the vector half of retrieval, so a later case can
 * only find it structurally.
 */
export interface WriteToGraphResult {
  graph_case_id: string;
  vertices: number;
  edges: number;
  edge_types: string[];
  embedded: boolean;
}

/**
 * One row of `GET /api/investigations`, straight off the `run` table.
 *
 * Responsibility: the counters a list needs and nothing that requires a join.
 * `validation_ok` is three-valued on purpose — null means the run has not
 * reached validation, which is different from having failed it.
 * Also the 202 body of `POST /api/investigations/{run_id}/cancel`: what a
 * caller wants back from a cancellation is the run's new state, and that is
 * this.
 */
export interface RunSummary {
  run_id: string;
  case_id: string;
  status: RunStatus;
  step: number;
  mode: RunMode;
  orchestrator: string;
  started_at: string;
  finished_at: string | null;
  tool_calls: number;
  tokens: number;
  usd: number;
  latency_s: number;
  validation_ok: boolean | null;
  error_code: string;
  batch_id: string | null;
}

/** What one run spent. The same four numbers the answer file reports. */
export interface RunCounters {
  tool_calls: number;
  tokens: number;
  usd: number;
  latency_s: number;
}

/** Why a run ended early, in the domain's own vocabulary. */
export interface RunError {
  code: string;
  message: string;
}

/**
 * `GET /api/investigations/{run_id}` — one run, with its budget and its error.
 *
 * Responsibility: everything a reconnecting console needs before it opens the
 * stream, so a page refresh mid-run does not have to replay the journal to
 * find out whether the run is still going.
 */
export interface RunDetail {
  run_id: string;
  case_id: string;
  status: RunStatus;
  step: number;
  mode: RunMode;
  orchestrator: string;
  started_at: string;
  finished_at: string | null;
  counters: RunCounters;
  budget: BudgetSnapshot;
  error: RunError | null;
  /** Journalled events so far — the replay length. */
  events: number;
}

/**
 * 202 from `POST /api/investigations` — where to watch it happen.
 *
 * Both URLs are returned because the console picks one: `stream_url` for a
 * live run, `events_url` when it would rather poll the journal.
 */
export interface RunAccepted {
  run_id: string;
  case_id: string;
  status: RunStatus;
  stream_url: string;
  events_url: string;
}

/**
 * One journalled event as it goes on the wire.
 *
 * Responsibility: mirror `api.sse.journal.SseEnvelope` so the replay
 * endpoint and the stream serve the identical shape — a client that
 * reconnects must not have to parse two.
 * Collaborators: `EventJournal` produces it; `format_sse` writes `seq`
 * as the SSE `id:` line.
 */
export interface EventEnvelope {
  v: number;
  /** == the SSE id: field. Monotonic per run from 1, so replay is seq > n. */
  seq: number;
  run_id: string;
  case_id: string;
  type: string;
  at: string;
  step: number | null;
  payload: Record<string, unknown>;
}

/**
 * `GET /api/investigations/{run_id}/events.json` — the journal, paged.
 *
 * `next` is the `seq` to ask for next, or null at the end of the journal;
 * it is the same cursor as `Last-Event-ID`, so a client can switch between
 * polling and streaming without translating.
 */
export interface EventPage {
  events: EventEnvelope[];
  next: number | null;
}

/**
 * One recommended action with the route the server recomputed for it.
 *
 * Responsibility: hold both routes when they disagree. `route` is what
 * `RoutingTable` returns for this action at this case's exposure and is the
 * only one any permission check consults; `answer_route` is what the answer
 * file claimed, kept so the console can badge the drift. HLD ADR-6.
 * Collaborators: `recomputed` builds it from a `Recommendation`;
 * `RoutePermissionPolicy` decides `may_execute`.
 */
export interface ActionRow {
  action: Action;
  route: Route;
  reason: string;
  /** The route the answer file stated, when it differs from the recomputed one. */
  answer_route: Route | null;
  route_changed: boolean;
  approvers: Role[];
  may_execute: boolean;
  execution_id: string | null;
  outcome: ExecutionOutcome | null;
  approval_id: string | null;
  approval_status: ApprovalStatus | null;
}

/**
 * `GET /api/cases/{case_id}/actions` — both phases, recomputed.
 *
 * Responsibility: the initial-to-final diff that is a quarter of the score,
 * plus the permission answer for the principal who asked. `role` is echoed
 * because `may_execute` is only meaningful next to the role it was computed
 * for, and the console has a role switcher.
 */
export interface ActionPlan {
  case_id: string;
  exposure_usd: number;
  role: Role;
  initial: ActionRow[];
  final: ActionRow[];
  what_changed: string;
}

/**
 * One attempt to execute an action, including the ones that were denied.
 *
 * Mirrors the `action_execution` table. `simulated` is false only for
 * `CREATE_CASE`, which really does write to the graph — an auditor reading
 * a row has to be able to tell a demonstration from a change.
 */
export interface ExecutionRecord {
  execution_id: string;
  at: string;
  case_id: string;
  run_id: string | null;
  action: Action;
  phase: Phase;
  route: Route;
  actor_id: string;
  actor_role: Role;
  approval_id: string | null;
  idempotency_key: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  outcome: ExecutionOutcome;
  simulated: boolean;
  request_id: string;
}

/**
 * One pending or decided approval, as the inbox renders it.
 *
 * Mirrors the `approval` table and adds the roles that may decide it, which
 * is otherwise a lookup the client would have to do against a table it should
 * not own.
 */
export interface ApprovalItem {
  approval_id: string;
  created_at: string;
  decided_at: string | null;
  case_id: string;
  action: Action;
  phase: Phase;
  route: Route;
  exposure_usd: number;
  requested_by: string;
  requested_role: Role;
  status: ApprovalStatus;
  decided_by: string | null;
  decided_role: Role | null;
  note: string;
  execution_id: string | null;
  approvers: Role[];
}

/**
 * `GET /api/approvals` — a page plus the tab counts beside it.
 *
 * The counts are unfiltered totals per status, not counts of `items`: the
 * inbox shows "3 pending" while displaying the approved tab.
 */
export interface ApprovalQueue {
  items: ApprovalItem[];
  total: number;
  counts: Partial<Record<ApprovalStatus, number>>;
}

/**
 * The decided approval and, when it was approved, what that executed.
 *
 * `execution` is null on a rejection and on an approval whose action could
 * not be executed; the approval itself is always returned, because the inbox
 * has to update either way.
 */
export interface ApprovalDecisionResult {
  approval: ApprovalItem;
  execution: ExecutionRecord | null;
}

/**
 * One row of the complete record of everything anyone asked this system to do.
 *
 * Mirrors the `audit` table. `route` is the recomputed route, never the
 * one the client sent — that is the difference between an audit log and a
 * transcript.
 */
export interface AuditItem {
  audit_id: string;
  at: string;
  request_id: string;
  actor_id: string;
  actor_role: Role;
  case_id: string;
  run_id: string | null;
  /** One of the 14 policy action names for an execution. A plain string because the log also records mutations that are not policy actions, such as a manual write-to-graph. */
  action: string;
  route: Route;
  approval_ref: string | null;
  idempotency_key: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  outcome: ExecutionOutcome;
  simulated: boolean;
}

/**
 * One vertex on the case canvas, with the attributes its shape encodes.
 *
 * `n_cards` is carried for device profiles specifically: a fingerprint
 * shared with 842 cards is not evidence, and the canvas fades it rather than
 * drawing it as strongly as a device seen on two.
 */
export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  label: string;
  /** The subject of the case — the alerted card, or the flagged transaction. */
  focus: boolean;
  /** A member of answer.case.affected_txn_ids; drawn with the danger ring. */
  affected: boolean;
  risk_score: number | null;
  amount_usd: number | null;
  at: string | null;
  n_cards: number | null;
  attrs: Record<string, string | number | boolean | null>;
}

/** One edge, named by its type in `graph/schema.gsql`. */
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  affected: boolean;
}

/** One row of the always-visible legend: a kind, its wording and its count. */
export interface GraphLegendEntry {
  kind: GraphNodeKind;
  label: string;
  count: number;
}

/**
 * `GET /api/graph/cases/{case_id}` — the subgraph, capped and labelled.
 *
 * Responsibility: return a drawable subgraph and say plainly when it is not
 * the whole one. `shown` and `total` exist so the truncation chip can
 * read "showing 60 of 214 — expand" rather than the canvas silently lying
 * about the size of a ring.
 */
export interface GraphCanvas {
  case_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  shown: number;
  total: number;
  legend: GraphLegendEntry[];
}

/**
 * One connected component the ring sweep kept.
 *
 * A component is not yet a ring: R6 requires it to be multi-customer,
 * time-concentrated and to match no documented pattern. `is_ring` is the
 * sweep's own verdict after those gates, so the console can draw a candidate
 * and a confirmed ring differently instead of implying every cluster counts.
 */
export interface RingComponent {
  component_id: string;
  cards: string[];
  devices: string[];
  seeds: string[];
  customers: string[];
  confirmed_fraud_cards: string[];
  exposure_usd: number;
  is_ring: boolean;
}

/**
 * One device profile and the cards seen on it inside the window.
 *
 * Recorded whether or not it formed a component, because a sweep that
 * reports only its hits cannot be told apart from a sweep that never ran.
 */
export interface RingDeviceLink {
  device: string;
  cards: string[];
  seeds: string[];
}

/**
 * `GET /api/graph/rings` — the ring sweep, as the CLI last wrote it.
 *
 * Read from `exploration/rings.json` rather than recomputed: the sweep
 * costs a few hundred graph calls and the console is not the place to spend
 * them. Serving the artefact also means the page and
 * `python -m sentinel explore rings` cannot disagree.
 *
 * `available` false is the honest answer when the sweep has never run —
 * distinct from a sweep that ran and found nothing, which is
 * `rings_found: 0` and is a result.
 */
export interface RingReport {
  available: boolean;
  generated_at: string;
  hops: number;
  window_days: number;
  max_device_cards: number;
  seeds: number;
  generic_profiles_skipped: number;
  two_hop_reach: number;
  two_hop_customers: number;
  components_found: number;
  rings_found: number;
  components: RingComponent[];
  focus: RingComponent | null;
  examined: RingDeviceLink[];
  note: string;
}

/**
 * `GET /api/cases/{case_id}/sar` — 200 even when `file` is false.
 *
 * A 404 for a case that decided *not* to file would hide `sar.reason`,
 * which is a graded field: the decision not to file is an answer, not an
 * absence.
 */
export interface SarBody {
  case_id: string;
  sar: SarReport;
  /** The plain-text filing `sar.txt` serves, so the UI needs no second call. */
  rendered: string;
}

/**
 * The two independent rankings behind one retrieval, before fusion.
 *
 * Shown side by side on purpose: vector finds a case that *reads* like this
 * one, structural finds a case that *is connected* to it, and the memory tab
 * is only convincing when both are visible.
 */
export interface RetrievalBreakdown {
  structural: RetrievalHit[];
  vector: RetrievalHit[];
}

/**
 * `GET /api/cases/{case_id}/memory` — what this case remembered, and from where.
 *
 * Responsibility: separate the bank's 5,565 closed investigations from the
 * cases Sentinel itself wrote earlier in the same run. That distinction is
 * the memory claim; collapsing the two lists would make it unverifiable.
 */
export interface MemoryBody {
  case_id: string;
  prior_closed_cases: RetrievalHit[];
  sentinel_cases: RetrievalHit[];
  /** The ids that reached answer.case.similar_prior_cases. */
  cited: string[];
  retrieval: RetrievalBreakdown;
}

/** One bucket of the probability histogram, half-open on the upper edge. */
export interface HistogramBin {
  lower: number;
  upper: number;
  count: number;
}

/** One case's outcome in a batch, as the benchmark table renders a row. */
export interface BenchmarkCase {
  case_id: string;
  ok: boolean;
  verdict: Verdict | null;
  fraud_probability: number | null;
  pattern: Pattern | null;
  actions: Action[];
  sar: boolean | null;
  tool_calls: number;
  tokens: number;
  elapsed_s: number;
  error: string;
  errors: ValidationFinding[];
}

/**
 * `GET /api/benchmark/report` — whether the last batch is defensible.
 *
 * Responsibility: the four ratios that say a run has stopped discriminating,
 * and the warnings derived from them. Half the benchmark is legitimate
 * activity, so a block rate over 0.50, a SAR rate over 0.20 or one verdict
 * covering more than 60 % of the pack is the shape of an over-eager agent —
 * the failure mode Guide.md names.
 * Collaborators: `sentinel.runner.BatchReport`, which computes the same
 * ratios for the CLI; this is its HTTP form.
 */
export interface BenchmarkReport {
  batch_id: string;
  total: number;
  valid: number;
  verdict_mix: Partial<Record<Verdict, number>>;
  block_rate: number;
  sar_rate: number;
  /** Share of cases where asking for evidence actually moved the answer. */
  changed_rate: number;
  probability_histogram: HistogramBin[];
  warnings: string[];
  elapsed_s: number;
  cases: BenchmarkCase[];
}

/** 202 from `POST /api/benchmark/runs` — the batch and where to watch it. */
export interface BenchmarkAccepted {
  batch_id: string;
  stream_url: string;
  case_ids: string[];
}

/**
 * `GET /api/cases/{case_id}` — everything the case screen opens with.
 *
 * Responsibility: one request for the whole screen. The alert is always
 * there; the answer, the run, the validation and the action plan are null
 * until the investigation produces them, so the screen renders in every state
 * a case can be in rather than only the finished one.
 */
export interface CaseDetail {
  alert: Alert;
  answer: AnswerFile | null;
  run: RunSummary | null;
  validation: ValidationBody | null;
  trace_available: boolean;
  actions: ActionPlan | null;
}

/**
 * RFC 9457 problem details — the body of every error this API returns.
 *
 * Declared here so the client has a type for it: a typed fetch wrapper that
 * cannot read `code` off a failure has to parse error prose instead, which
 * is how a demo ends up showing "Something went wrong".
 */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string;
  request_id: string;
}

/** The approval a denied execute just enqueued, inside the 403 body. */
export interface ApprovalRef {
  approval_id: string;
  status: ApprovalStatus;
  href: string;
}

/**
 * The 403 that names who can approve.
 *
 * Unusual REST, taken deliberately: a denied execute returns 403 *and*
 * idempotently enqueues the approval, so the exit test is one click from
 * blocked to inbox. `on_denied: "reject"` opts out and leaves `approval`
 * null.
 */
export interface ForbiddenRouteProblem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string;
  request_id: string;
  action: Action;
  required_route: Route;
  your_role: Role;
  roles_that_can_approve: Role[];
  approval: ApprovalRef | null;
}

// ── API requests and query strings ──────────────────────────────────────────

/**
 * `POST /api/investigations`.
 *
 * `force` is what makes a second run on a case legal: without it the
 * endpoint 409s on `run_already_active`, which is the behaviour that keeps
 * a double-clicked demo from running the same alert twice.
 */
export interface StartInvestigationRequest {
  case_id: string;
  mode?: RunMode;
  force?: boolean;
  /** Named orchestrator to run. Empty means the configured default. */
  orchestrator?: string;
}

/**
 * `POST /api/cases/{case_id}/write-to-graph`.
 *
 * `force` re-writes a case already in the graph. The write is an upsert, so
 * this is safe; the flag exists so an accidental second click 409s instead.
 */
export interface WriteToGraphRequest {
  force?: boolean;
}

/**
 * `POST /api/actions/execute` — the one door to every side effect.
 *
 * `phase` is part of the request because an approval is idempotent on
 * `(case_id, action, phase)`: executing the initial `VERIFY_WITH_CUSTOMER`
 * and the final one are two different acts on the same case.
 */
export interface ExecuteRequest {
  case_id: string;
  action: Action;
  phase?: Phase;
  payload?: Record<string, unknown>;
  on_denied?: DenialPolicy;
}

/** `POST /api/approvals/{id}/decision`. */
export interface ApprovalDecisionRequest {
  decision: ApprovalDecision;
  note?: string;
}

/** `POST /api/approvals/{id}/approve` — the decision endpoint's one-verb alias. */
export interface ApprovalApproveRequest {
  note?: string;
}

/**
 * `POST /api/benchmark/runs` — run the pack.
 *
 * `concurrency` defaults to 1 because the cases are not independent: a
 * later case may retrieve the `FraudCase` an earlier one wrote, and running
 * them in parallel makes which memories existed a race.
 */
export interface BenchmarkRequest {
  /** Empty means every alert in the case pack. */
  case_ids?: string[];
  order?: BatchOrder;
  concurrency?: number;
  mode?: RunMode;
}

/** `GET /api/cases` — filters over the queue. */
export interface CaseQuery {
  status?: CaseStatus | null;
  verdict?: Verdict | null;
  trigger_type?: TriggerType | null;
  sort?: CaseSort;
  /** Free text over case id, card, customer and summary. */
  q?: string;
  limit?: number;
  offset?: number;
}

/** `GET /api/cases/{case_id}/trace` — which run's trace. Latest when empty. */
export interface TraceQuery {
  run_id?: string;
}

/**
 * `GET /api/cases/{case_id}/validation`.
 *
 * `graph` is false by default because the graph half costs a round trip per
 * batch of ids and the pure half is what blocks a write.
 */
export interface ValidationQuery {
  graph?: boolean;
}

/** `GET /api/cases/{case_id}/actions` — one phase, or both when null. */
export interface ActionsQuery {
  phase?: Phase | null;
}

/** `GET /api/investigations`. */
export interface RunQuery {
  case_id?: string;
  status?: RunStatus | null;
  limit?: number;
}

/**
 * `GET /api/investigations/{run_id}/events` — the SSE query fallbacks.
 *
 * `EventSource` cannot send headers, so the role and the resume point have
 * to be expressible in the URL. The console uses `fetch` and sends both as
 * headers; these exist so a `curl` in the demo, and a browser that falls
 * back, still work. HLD ADR-5.
 */
export interface StreamQuery {
  last_event_id?: number;
  /** Replay speed multiplier. Ignored on a live run. */
  speed?: number;
  role?: Role | null;
}

/**
 * `GET /api/investigations/{run_id}/events.json` — a `seq` range.
 *
 * `from` is a Python keyword, so the field is `from_seq` and the alias is
 * what appears on the wire and in the generated contract.
 */
export interface EventRangeQuery {
  from?: number;
  to?: number | null;
  limit?: number;
}

/** `GET /api/actions/executions`. `status` filters on the outcome. */
export interface ExecutionQuery {
  case_id?: string;
  status?: ExecutionOutcome | null;
  limit?: number;
}

/** `GET /api/approvals`. */
export interface ApprovalQuery {
  status?: ApprovalStatus | null;
  route?: Route | null;
  case_id?: string;
}

/** `GET /api/audit`. */
export interface AuditQuery {
  case_id?: string;
  actor?: string;
  action?: string;
  limit?: number;
  offset?: number;
}

/**
 * `GET /api/graph/cases/{case_id}`.
 *
 * `max_nodes` defaults low and the response says when it bound: ungated,
 * the 116 device profiles carrying 24,653 links merge the graph into one
 * component and the canvas becomes a hairball rather than a case.
 */
export interface GraphQuery {
  depth?: number;
  include_ring?: boolean;
  max_nodes?: number;
}

// ── Paged responses ─────────────────────────────────────────────────────────

/**
 * `{items, total}` — the envelope every list endpoint returns.
 *
 * Responsibility: one page of rows plus the unpaged total, so the queue can
 * render "20 of 20" without a second count request.
 * Collaborators: the list endpoints; the TypeScript emitter, which turns this
 * into `Page<T>` and each concrete parametrisation into a named alias.
 */
export interface Page<T> {
  items: T[];
  total: number;
}

export type CasePage = Page<CaseSummary>;

export type RunPage = Page<RunSummary>;

export type ExecutionPage = Page<ExecutionRecord>;

export type AuditPage = Page<AuditItem>;

export type ApprovalPage = Page<ApprovalItem>;
