/**
 * Ask Sentinel's client: the v2 service, reached through the `/v2` rewrite in next.config.ts.
 *
 * Unlike the rest of the console, these calls do not send the role switcher's
 * claim. What an answer may draw on is decided by a signed-in identity — the
 * server filters every source by that identity's permissions before the model
 * sees anything — so the panel signs in with a username and password and the
 * server keeps the session in an HttpOnly cookie this code never reads.
 */

export type V2Role = "analyst" | "team_lead" | "fraud_manager" | "regulator";

export interface Viewer {
  actor_id: string;
  actor_role: V2Role;
  authenticated: boolean;
  permissions: string[];
}

export interface ScopeItem {
  what: string;
  permission: string;
  granted: boolean;
}

export interface Upload {
  doc_id: string;
  name: string;
  chars: number;
  uploaded_at: string;
  instruction_like_spans: string[];
}

export interface AnswerSource {
  marker: string;
  id: string;
  kind: string;
  title: string;
  link: string;
  snippet: string;
  found_by: string[];
  cited: boolean;
}

export interface Answer {
  answer: string;
  support: "supported" | "partial" | "unsupported" | "not_found" | "refused";
  sources: AnswerSource[];
  withheld: { permission: string; what: string }[];
  guardrails: {
    role?: string;
    sources_searched: number;
    sources_withheld_by_role: number;
    instruction_like_text_in_question?: string[];
    instruction_like_text_in_sources?: string[];
    citations_removed?: number;
    read_only?: boolean;
  };
  notes: string[];
  model: string | null;
  tokens: number;
  latency_s: number;
}

export interface SourceBody {
  id: string;
  kind: string;
  title: string;
  text: string;
  link: string;
}

export class V2Error extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "V2Error";
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new V2Error(0, "The Sentinel v2 service is not reachable. Start it with: cd backend && uvicorn api_v2.app:app --port 8100");
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : `${r.status} ${r.statusText}`;
    const hint = r.status >= 500 && !body?.detail
      ? "The Sentinel v2 service did not answer. Start it with: cd backend && uvicorn api_v2.app:app --port 8100"
      : detail;
    throw new V2Error(r.status, hint);
  }
  return body as T;
}

export const assistantApi = {
  scope: () => call<{ viewer: Viewer; scope: ScopeItem[]; model: string; uploads: Upload[] }>("/v2/assistant/scope"),
  login: (username: string, password: string) =>
    call<{ username: string; role: V2Role }>("/v2/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => call<{ signed_out: boolean }>("/v2/logout", { method: "POST" }),
  ask: (question: string, history: { role: "user" | "assistant"; text: string }[]) =>
    call<Answer>("/v2/assistant/ask", { method: "POST", body: JSON.stringify({ question, history }) }),
  source: (id: string) => call<SourceBody>(`/v2/assistant/sources/${encodeURIComponent(id)}`),
  upload: (name: string, text: string) =>
    call<Upload>("/v2/assistant/uploads", { method: "POST", body: JSON.stringify({ name, text }) }),
  remove: (docId: string) => call<{ removed: string }>(`/v2/assistant/uploads/${encodeURIComponent(docId)}`, { method: "DELETE" }),
};

export const V2_ROLE_LABELS: Record<V2Role, string> = {
  analyst: "Analyst",
  team_lead: "Team lead",
  fraud_manager: "Fraud manager",
  regulator: "Regulator",
};
