/**
 * The one place this app talks to the backend.
 *
 * Three things are centralised here because getting any of them wrong in one
 * component and right in another is worse than getting them wrong everywhere.
 *
 * **The role header.** Every request carries `X-Sentinel-Role`, read from the
 * role switcher. The server recomputes the permission from it; the client
 * never decides what it may do, it only says who it is.
 *
 * **The problem envelope.** Every deliberate failure comes back as
 * `application/problem+json`, and a 403 on an action carries the approval it
 * just enqueued. `ApiProblem` keeps that structured so the action panel can
 * link straight to the inbox instead of showing a string.
 *
 * **Types.** Nothing here declares a shape. Everything is imported from
 * `generated/contract.ts`, which the backend emits from its own models — see
 * HLD ADR-7. A hand-typed `"BLOCK_ALL_CARDS"` anywhere in this app is how the
 * third copy drifts.
 */

import type {
  ActionPlan,
  ApprovalDecisionResult,
  ApprovalPage,
  AuditPage,
  BenchmarkAccepted,
  BenchmarkReport,
  CaseDetail,
  CasePage,
  ExecuteRequest,
  ExecutionRecord,
  GraphCanvas,
  MemoryBody,
  Meta,
  Role,
  RunAccepted,
  RingReport,
  RunDetail,
  SarBody,
  TraceBody,
  ValidationBody,
} from "@/lib/generated/contract";

/** Empty by default: the console API is reached through this app's own `/api` proxy
 *  (next.config.ts), so the signed session cookie from sign-in goes with every call. */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "";

/** Where the role lives between reloads. Per-viewer convenience, nothing more. */
const ROLE_KEY = "sentinel.role";

export const DEFAULT_ROLE: Role = "analyst";

export function readRole(): Role {
  // Storage throws in a private window and on a blocked site; a console that
  // cannot remember the role still has to render.
  try {
    const stored = window.localStorage.getItem(ROLE_KEY);
    if (stored === "analyst" || stored === "team_lead" || stored === "fraud_manager") {
      return stored;
    }
  } catch {
    /* no storage; the default is correct */
  }
  return DEFAULT_ROLE;
}

export function writeRole(role: Role): void {
  try {
    window.localStorage.setItem(ROLE_KEY, role);
  } catch {
    /* no storage; the role still applies for this page's lifetime */
  }
}

/**
 * A deliberate failure from the API, with its problem body intact.
 *
 * The body is the useful part. A 403 on an action names the route, the roles
 * that can approve it, and the approval it enqueued — everything the panel
 * needs to offer the next step without asking again.
 */
export class ApiProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly detail: string;
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${status}`;
    super(detail);
    this.name = "ApiProblem";
    this.status = status;
    this.code = typeof body.code === "string" ? body.code : "error";
    this.title = typeof body.title === "string" ? body.title : "Something went wrong";
    this.detail = detail;
    this.body = body;
  }

  /** The approval a denied action enqueued, when there is one. */
  get approval(): { approval_id: string; status: string; href: string } | null {
    const value = this.body.approval;
    return value && typeof value === "object"
      ? (value as { approval_id: string; status: string; href: string })
      : null;
  }

  get approvers(): string[] {
    const value = this.body.roles_that_can_approve;
    return Array.isArray(value) ? (value as string[]) : [];
  }

  get requiredRoute(): string {
    return typeof this.body.required_route === "string" ? this.body.required_route : "";
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function url(path: string, query?: Query): string {
  const origin = API_BASE || (typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:3000");
  const target = new URL(`${origin}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined && value !== "") {
      target.searchParams.set(key, String(value));
    }
  }
  return target.toString();
}

async function request<T>(
  path: string,
  { query, method = "GET", body, role, idempotencyKey }: {
    query?: Query;
    method?: "GET" | "POST";
    body?: unknown;
    role?: Role;
    idempotencyKey?: string;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { "X-Sentinel-Role": role ?? DEFAULT_ROLE };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const response = await fetch(url(path, query), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401 && typeof window !== "undefined") {
    // The session expired or was never there: back to the front door, then back here.
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }
  if (!response.ok) {
    let parsed: Record<string, unknown> = {};
    try {
      parsed = (await response.json()) as Record<string, unknown>;
    } catch {
      parsed = { detail: response.statusText };
    }
    throw new ApiProblem(response.status, parsed);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Every endpoint the console uses, typed from the generated contract. */
export const api = {
  meta: () => request<Meta>("/api/meta"),
  health: () => request<{ status: string; uptime_s: number }>("/api/health"),

  cases: (query: Query, role: Role) => request<CasePage>("/api/cases", { query, role }),
  case: (id: string, role: Role) => request<CaseDetail>(`/api/cases/${id}`, { role }),
  answer: (id: string, role: Role) => request<unknown>(`/api/cases/${id}/answer`, { role }),
  trace: (id: string, role: Role) => request<TraceBody>(`/api/cases/${id}/trace`, { role }),
  validation: (id: string, graph: boolean, role: Role) =>
    request<ValidationBody>(`/api/cases/${id}/validation`, { query: { graph }, role }),
  sar: (id: string, role: Role) => request<SarBody>(`/api/cases/${id}/sar`, { role }),
  memory: (id: string, role: Role) => request<MemoryBody>(`/api/cases/${id}/memory`, { role }),
  actions: (id: string, role: Role) => request<ActionPlan>(`/api/cases/${id}/actions`, { role }),
  canvas: (id: string, role: Role) =>
    request<GraphCanvas>(`/api/graph/cases/${id}`, { role }),
  rings: (role: Role) => request<RingReport>("/api/graph/rings", { role }),

  startInvestigation: (caseId: string, role: Role) =>
    request<RunAccepted>("/api/investigations", {
      method: "POST",
      body: { case_id: caseId, mode: "live", force: true },
      role,
    }),
  run: (runId: string, role: Role) => request<RunDetail>(`/api/investigations/${runId}`, { role }),

  execute: (body: ExecuteRequest, role: Role, idempotencyKey?: string) =>
    request<ExecutionRecord>("/api/actions/execute", {
      method: "POST",
      body,
      role,
      idempotencyKey,
    }),

  approvals: (query: Query, role: Role) =>
    request<ApprovalPage>("/api/approvals", { query, role }),
  approve: (approvalId: string, note: string, role: Role) =>
    request<ApprovalDecisionResult>(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      body: { decision: "approve", note },
      role,
    }),
  reject: (approvalId: string, note: string, role: Role) =>
    request<ApprovalDecisionResult>(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      body: { decision: "reject", note },
      role,
    }),

  audit: (query: Query, role: Role) => request<AuditPage>("/api/audit", { query, role }),
  runAll: (role: Role) =>
    request<BenchmarkAccepted>("/api/benchmark/runs", {
      method: "POST",
      body: { case_ids: [], order: "chronological", concurrency: 1, mode: "live" },
      role,
    }),
  benchmark: (role: Role) => request<BenchmarkReport>("/api/benchmark/report", { role }),
};

/** The SSE URL for a run. Used by `useInvestigationStream`. */
export const streamUrl = (runId: string, lastEventId: number, role: Role) =>
  url(`/api/investigations/${runId}/events`, {
    last_event_id: lastEventId > 0 ? lastEventId : undefined,
    role,
  });
