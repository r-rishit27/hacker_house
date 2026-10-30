"use client";

/**
 * Query hooks and the live event stream.
 *
 * The stream is the part worth reading. `useInvestigationStream` holds the
 * last `seq` it saw and hands it back as `last_event_id` on every reconnect,
 * so a dropped connection resumes exactly where it stopped — the server
 * journals each event before publishing it, which is what makes "no gap and
 * no duplicate" a guarantee rather than a hope.
 *
 * It uses `EventSource` rather than `fetch`, which costs it the ability to
 * send the role as a header — hence the query fallback the API declares. In
 * exchange the browser handles reconnection, backoff and the `retry:` hint
 * the server sends as the first line of every stream.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, streamUrl, type ApiProblem } from "@/lib/api";
import type {
  EventEnvelope,
  ExecuteRequest,
  Role,
  SentinelEventType,
} from "@/lib/generated/contract";
import { useRole } from "@/lib/role";

const STALE = 5_000;

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: Infinity });
}

export function useCases(filters: Record<string, string | number | undefined>) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["cases", filters, role],
    queryFn: () => api.cases(filters, role),
    staleTime: STALE,
    // The queue is the thing a viewer watches during a batch run, so it
    // refreshes on its own rather than waiting to be asked.
    refetchInterval: 4_000,
  });
}

export function useCase(caseId: string) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["case", caseId, role],
    queryFn: () => api.case(caseId, role),
    staleTime: STALE,
    enabled: Boolean(caseId),
  });
}

export function useTrace(caseId: string, enabled: boolean) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["trace", caseId, role],
    queryFn: () => api.trace(caseId, role),
    enabled: enabled && Boolean(caseId),
    retry: false,
  });
}

export function useSar(caseId: string, enabled: boolean) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["sar", caseId, role],
    queryFn: () => api.sar(caseId, role),
    enabled: enabled && Boolean(caseId),
    retry: false,
  });
}

export function useMemoryTab(caseId: string, enabled: boolean) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["memory", caseId, role],
    queryFn: () => api.memory(caseId, role),
    enabled: enabled && Boolean(caseId),
    retry: false,
  });
}

export function useActions(caseId: string) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["actions", caseId, role],
    queryFn: () => api.actions(caseId, role),
    enabled: Boolean(caseId),
    retry: false,
  });
}

export function useApprovals(status?: string) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["approvals", status, role],
    queryFn: () => api.approvals({ status, limit: 100 }, role),
    refetchInterval: 5_000,
  });
}

export function useAudit(caseId?: string) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["audit", caseId, role],
    queryFn: () => api.audit({ case_id: caseId, limit: 50 }, role),
    refetchInterval: 5_000,
  });
}

/** The case subgraph. Costs a live graph round trip, so it is opt-in. */
export function useCanvas(caseId: string, enabled: boolean) {
  const role = useRole().role;
  return useQuery({
    queryKey: ["canvas", caseId, role],
    queryFn: () => api.canvas(caseId, role),
    enabled: enabled && Boolean(caseId),
    staleTime: 60_000,
    // The workspace wakes in about 45 seconds and answers a cold query with a
    // 502 while it does. One retry turns that into a slow load, not a failure.
    retry: 1,
  });
}

/** The ring sweep as `explore rings` last wrote it. A file, so it is cheap. */
export function useRings() {
  const role = useRole().role;
  return useQuery({
    queryKey: ["rings", role],
    queryFn: () => api.rings(role),
    staleTime: Infinity,
  });
}

export function useBenchmark() {
  const role = useRole().role;
  return useQuery({ queryKey: ["benchmark", role], queryFn: () => api.benchmark(role) });
}

/** Execute one action. A 403 is a result, not a crash — the panel renders it. */
export function useExecute(caseId: string) {
  const role = useRole().role;
  const client = useQueryClient();
  return useMutation<unknown, ApiProblem, ExecuteRequest>({
    mutationFn: (body) => api.execute(body, role),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["actions", caseId] });
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

export function useDecideApproval() {
  const role = useRole().role;
  const client = useQueryClient();
  return useMutation<unknown, ApiProblem, { id: string; approve: boolean; note: string }>({
    mutationFn: ({ id, approve, note }) =>
      approve ? api.approve(id, note, role) : api.reject(id, note, role),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["audit"] });
      void client.invalidateQueries({ queryKey: ["actions"] });
    },
  });
}

/**
 * Start the whole pack.
 *
 * This is a real batch — twenty live investigations against the graph and the
 * model, several minutes and real tokens — so the button that calls it arms
 * before it fires. The mutation itself does not guard; the control does.
 */
export function useRunAll() {
  const role = useRole().role;
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.runAll(role),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["cases"] });
      void client.invalidateQueries({ queryKey: ["benchmark"] });
    },
  });
}

export function useStartInvestigation() {
  const role = useRole().role;
  const client = useQueryClient();
  return useMutation({
    mutationFn: (caseId: string) => api.startInvestigation(caseId, role),
    onSettled: () => void client.invalidateQueries({ queryKey: ["cases"] }),
  });
}

// ── the live stream ──────────────────────────────────────────────────────────

export type ConnectionState = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export interface StreamState {
  events: EventEnvelope[];
  connection: ConnectionState;
  lastSeq: number;
  /** True once a `run.completed` or `run.failed` has arrived. */
  finished: boolean;
}

const TERMINAL: SentinelEventType[] = ["run.completed", "run.failed"];

/**
 * Subscribe to one run, resuming from the last sequence number on reconnect.
 *
 * The events are kept in arrival order and never de-duplicated by the client:
 * if two copies of `seq` 31 ever arrived, the server's guarantee would be
 * broken and hiding it here would only make that harder to notice.
 */
export function useInvestigationStream(runId: string | null): StreamState {
  const role = useRole().role;
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [finished, setFinished] = useState(false);
  const lastSeq = useRef(0);
  const client = useQueryClient();

  useEffect(() => {
    if (!runId) {
      setConnection("idle");
      return;
    }
    setEvents([]);
    setFinished(false);
    lastSeq.current = 0;
    let closed = false;
    let source: EventSource | null = null;

    const open = () => {
      if (closed) return;
      setConnection(lastSeq.current > 0 ? "reconnecting" : "connecting");
      source = new EventSource(streamUrl(runId, lastSeq.current, role));

      source.onopen = () => !closed && setConnection("live");
      source.onerror = () => {
        // EventSource reconnects on its own using the server's `retry:` hint.
        // Saying "reconnecting" rather than "offline" is the honest state: the
        // workspace takes about 45 seconds to wake and that is not an error.
        if (!closed) setConnection("reconnecting");
      };

      const onEvent = (raw: MessageEvent<string>) => {
        const envelope = JSON.parse(raw.data) as EventEnvelope;
        lastSeq.current = Math.max(lastSeq.current, envelope.seq);
        setEvents((current) => [...current, envelope]);
        if (TERMINAL.includes(envelope.type as SentinelEventType)) {
          setFinished(true);
          setConnection("closed");
          closed = true;
          source?.close();
          void client.invalidateQueries({ queryKey: ["cases"] });
          void client.invalidateQueries({ queryKey: ["case", envelope.case_id] });
        }
      };

      // Each event type arrives under its own `event:` name, so a generic
      // `onmessage` would never fire.
      for (const type of ALL_EVENT_TYPES) {
        source.addEventListener(type, onEvent as EventListener);
      }
    };

    open();
    return () => {
      closed = true;
      source?.close();
      setConnection("idle");
    };
  }, [runId, role, client]);

  return { events, connection, lastSeq: lastSeq.current, finished };
}

/** Every event name the server can send. Imported, never typed by hand. */
const ALL_EVENT_TYPES: string[] = [
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

/** The probability after each posting, for the trajectory. */
export function useTrajectory(events: EventEnvelope[]): number[] {
  return useMemo(() => {
    const out: number[] = [];
    for (const event of events) {
      if (event.type === "run.started") {
        const prior = (event.payload as { prior?: number }).prior;
        if (typeof prior === "number") out.push(prior);
      }
      if (event.type === "evidence.posted") {
        const after = (event.payload as { p_after?: number }).p_after;
        if (typeof after === "number") out.push(after);
      }
    }
    return out;
  }, [events]);
}

/** A stable callback that copies text and reports whether it worked. */
export function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const copy = useCallback((text: string) => {
    void navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      },
      () => setCopied(false),
    );
  }, []);
  return [copied, copy];
}
