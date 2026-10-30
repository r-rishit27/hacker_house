"""The TypeScript contract emitter: one vocabulary, two languages.

    python -m api.contract emit --out frontend/src/lib/generated/contract.ts
    python -m api.contract emit --out <same path> --check      # CI

Fourteen action names, three routes, nine more closed vocabularies, sixteen
event types and sixty-odd response shapes are needed on both sides of the
wire. Without this module the frontend hand-types them, and the copy is wrong
the first time an enum gains a member — silently, because a string literal
that no longer matches any backend value is still a valid TypeScript string.
That is HLD ADR-7, and this is the build-time half of it; ``GET /api/meta`` is
the runtime half.

Everything here is read, not written: the enums come from
``sentinel.domain.enums``, the thresholds from ``PolicyConfig``'s own
dataclass fields, the tool names from the catalogue, the event union from
``EVENT_TYPES`` and every interface from a Pydantic model's
``model_fields``. Three completeness checks make a gap a build failure rather
than a missing type — an enum referenced but not exported, a model referenced
but not emitted, and a model defined in ``api.schemas`` that no section
lists.

Two deliberate translations, because JSON and TypeScript disagree:

- **Optional means null, not absent.** ``x: str | None`` emits ``x: string |
  null``. A response field is always present; it is its *value* that can be
  null, and ``x?: string`` would make every consumer handle a case the API
  never produces.
- **Request fields with defaults emit ``?``.** The other direction: a client
  that must spell out every default is a client that will get one wrong.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from datetime import datetime
from enum import Enum
from inspect import cleandoc
from pathlib import Path
from textwrap import dedent
from types import UnionType
from typing import Any, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel

from api import schemas
from api.schemas import EVENT_PAYLOADS, Page
from api.sse.journal import ENVELOPE_VERSION
from sentinel.agents.emitter import EVENT_TYPES, TERMINAL_EVENTS
from sentinel.domain.alert import Alert
from sentinel.domain.answer import (
    ActionRecommendation,
    AnswerFile,
    Case,
    Evidence,
    EvidenceRequest,
    NextBestActions,
    SarReport,
)
from sentinel.domain.enums import (
    APPROVERS,
    AUTO_ACTIONS,
    FIXED_ROUTES,
    Action,
    CaseStatus,
    CustomerResponse,
    EvidenceSource,
    Pattern,
    RequestType,
    Role,
    Route,
    TriggerType,
    Verdict,
)
from sentinel.evidence.ledger import GROUP_CAP
from sentinel.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from sentinel.tools.registry import TOOL_NAMES

EXIT_OK = 0
EXIT_ERROR = 1

#: The enums that become an exported const array plus a union type, and the
#: name of that array. Order is the order they appear in the file.
ENUM_EXPORTS: tuple[tuple[type[Enum], str], ...] = (
    (Action, "ACTIONS"),
    (Route, "ROUTES"),
    (Role, "ROLES"),
    (Verdict, "VERDICTS"),
    (CaseStatus, "CASE_STATUSES"),
    (Pattern, "PATTERNS"),
    (EvidenceSource, "EVIDENCE_SOURCES"),
    (RequestType, "REQUEST_TYPES"),
    (TriggerType, "TRIGGER_TYPES"),
    (CustomerResponse, "CUSTOMER_RESPONSES"),
    (schemas.StepName, "STEP_NAMES"),
    (schemas.RunStatus, "RUN_STATUSES"),
    (schemas.RunMode, "RUN_MODES"),
    (schemas.Phase, "PHASES"),
    (schemas.ExecutionOutcome, "EXECUTION_OUTCOMES"),
    (schemas.ApprovalStatus, "APPROVAL_STATUSES"),
    (schemas.ApprovalDecision, "APPROVAL_DECISIONS"),
    (schemas.DenialPolicy, "DENIAL_POLICIES"),
    (schemas.CaseSort, "CASE_SORTS"),
    (schemas.BatchOrder, "BATCH_ORDERS"),
    (schemas.ServiceStatus, "SERVICE_STATUSES"),
    (schemas.RetrievalKind, "RETRIEVAL_KINDS"),
    (schemas.GraphNodeKind, "GRAPH_NODE_KINDS"),
)

#: The answer file, emitted from the models the grader's own contract uses.
ANSWER_MODELS: tuple[type[BaseModel], ...] = (
    Evidence,
    EvidenceRequest,
    ActionRecommendation,
    NextBestActions,
    SarReport,
    Case,
    AnswerFile,
    Alert,
)

#: Value objects the event payloads are built from. Emitted before them so the
#: file reads top-down.
EVENT_VALUE_MODELS: tuple[type[BaseModel], ...] = (
    schemas.BudgetSnapshot,
    schemas.ValidationFinding,
    schemas.RetrievalHit,
    schemas.GateOutcome,
    schemas.SarSummary,
    schemas.StopSummary,
)

#: The REST responses, in the order of TECHNICAL.md's endpoint table.
RESPONSE_MODELS: tuple[type[BaseModel], ...] = (
    schemas.Health,
    schemas.ReadyCheck,
    schemas.Ready,
    schemas.MetaEnums,
    schemas.MetaRouting,
    schemas.MetaThresholds,
    schemas.Meta,
    schemas.CaseSummary,
    schemas.ValidationBody,
    schemas.PostingView,
    schemas.TraceStep,
    schemas.TraceBody,
    schemas.WriteToGraphResult,
    schemas.RunSummary,
    schemas.RunCounters,
    schemas.RunError,
    schemas.RunDetail,
    schemas.RunAccepted,
    schemas.EventEnvelope,
    schemas.EventPage,
    schemas.ActionRow,
    schemas.ActionPlan,
    schemas.ExecutionRecord,
    schemas.ApprovalItem,
    schemas.ApprovalQueue,
    schemas.ApprovalDecisionResult,
    schemas.AuditItem,
    schemas.GraphNode,
    schemas.GraphEdge,
    schemas.GraphLegendEntry,
    schemas.GraphCanvas,
    schemas.RingComponent,
    schemas.RingDeviceLink,
    schemas.RingReport,
    schemas.SarBody,
    schemas.RetrievalBreakdown,
    schemas.MemoryBody,
    schemas.HistogramBin,
    schemas.BenchmarkCase,
    schemas.BenchmarkReport,
    schemas.BenchmarkAccepted,
    schemas.CaseDetail,
    schemas.Problem,
    schemas.ApprovalRef,
    schemas.ForbiddenRouteProblem,
)

#: Request bodies and query strings. Fields with defaults emit as optional.
REQUEST_MODELS: tuple[type[BaseModel], ...] = (
    schemas.StartInvestigationRequest,
    schemas.WriteToGraphRequest,
    schemas.ExecuteRequest,
    schemas.ApprovalDecisionRequest,
    schemas.ApprovalApproveRequest,
    schemas.BenchmarkRequest,
    schemas.CaseQuery,
    schemas.TraceQuery,
    schemas.ValidationQuery,
    schemas.ActionsQuery,
    schemas.RunQuery,
    schemas.StreamQuery,
    schemas.EventRangeQuery,
    schemas.ExecutionQuery,
    schemas.ApprovalQuery,
    schemas.AuditQuery,
    schemas.GraphQuery,
)

#: Models in ``api.schemas`` that are deliberately not emitted as interfaces.
#: ``Row`` is a configuration base with no fields; ``Page`` is emitted by hand
#: because TypeScript writes its generic differently.
NOT_EMITTED: frozenset[str] = frozenset({"Row", "Page"})

#: Prose for fields on models this module does not own. The domain models use
#: ``#:`` comments, which Python discards at import, so the two notes the
#: console actually needs are kept here — the only hand-written field text in
#: the emitted file.
FIELD_NOTES: Mapping[tuple[str, str], str] = {
    ("Alert", "opened_at"): (
        "When the alert was raised — one to six hours after the flagged "
        "transaction, never zero. Anchor a window on the transaction, not on this."
    ),
    ("Alert", "risk_score"): (
        "The score quoted in the alert, or null on the nine that quoted none. "
        "Never a risk-band input; the model's score is on the transaction."
    ),
    ("Case", "connected_device_profiles"): (
        '"DeviceInfo | OS | browser | screen" — the label form, not a vertex key.'
    ),
}

_RST_ROLE = re.compile(r":(?:class|meth|attr|mod|func|data):")
_SCALARS: Mapping[object, str] = {
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    datetime: "string",
    type(None): "null",
}


class TypeScriptEmitter:
    """Turns Python enums and Pydantic models into one TypeScript module.

    Responsibility: the translation and the bookkeeping that proves it is
    complete — which enums and which models were referenced, so a type that is
    used but never exported fails the emit instead of the frontend's build.
    Collaborators: ``sentinel.domain.enums`` and ``api.schemas`` supply
    everything it writes; :func:`build` sequences the sections.
    """

    def __init__(self) -> None:
        self._enums: set[type[Enum]] = set()
        self._models: set[type[BaseModel]] = set()

    # ── bookkeeping ──────────────────────────────────────────────────────────

    @property
    def referenced_enums(self) -> frozenset[type[Enum]]:
        """Every enum a field mentioned. Checked against ``ENUM_EXPORTS``."""
        return frozenset(self._enums)

    @property
    def referenced_models(self) -> frozenset[type[BaseModel]]:
        """Every model a field mentioned. Checked against what was emitted."""
        return frozenset(self._models)

    # ── types ────────────────────────────────────────────────────────────────

    def type_of(self, annotation: object) -> str:
        """One Python annotation as a TypeScript type.

        Raises ``TypeError`` for anything unmapped rather than falling back to
        ``unknown``: a silent ``unknown`` is how a typed client stops being
        typed, and it would be found in a component rather than here.
        """
        if annotation is Any:
            return "unknown"
        scalar = _SCALARS.get(annotation)
        if scalar is not None:
            return scalar
        if isinstance(annotation, TypeVar):
            return annotation.__name__
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            self._enums.add(annotation)
            return annotation.__name__
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return self._model_name(annotation)

        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is Literal:
            return " | ".join(_quote(str(value)) for value in args)
        if origin in (Union, UnionType):
            return self._union(args)
        if origin in (list, set, frozenset, Sequence, tuple):
            return self._array(args)
        if origin in (dict, Mapping):
            return self._record(args)
        raise TypeError(f"no TypeScript mapping for {annotation!r}")

    def _union(self, args: tuple[object, ...]) -> str:
        # `null` last, always: `string | null` reads as "a string, or nothing",
        # which is what the field means.
        parts = [self.type_of(arg) for arg in args if arg is not type(None)]
        if type(None) in args:
            parts.append("null")
        return " | ".join(dict.fromkeys(parts))

    def _array(self, args: tuple[object, ...]) -> str:
        inner = self.type_of(args[0]) if args else "unknown"
        return f"({inner})[]" if "|" in inner else f"{inner}[]"

    def _record(self, args: tuple[object, ...]) -> str:
        key, value = (*args, Any, Any)[:2]
        rendered = f"Record<{self.type_of(key)}, {self.type_of(value)}>"
        if isinstance(key, type) and issubclass(key, Enum):
            # A map keyed by an enum is never complete — FIXED_ROUTES holds 13
            # of the 14 actions — so claiming every key would make the missing
            # one a runtime undefined the type system promised could not happen.
            return f"Partial<{rendered}>"
        return rendered

    def _model_name(self, model: type[BaseModel]) -> str:
        """The TypeScript name, resolving ``Page[CaseSummary]`` to ``Page<CaseSummary>``."""
        generic = getattr(model, "__pydantic_generic_metadata__", None)
        if generic and generic.get("args"):
            origin = generic["origin"]
            inner = ", ".join(self.type_of(arg) for arg in generic["args"])
            return f"{origin.__name__}<{inner}>"
        self._models.add(model)
        return model.__name__

    # ── declarations ─────────────────────────────────────────────────────────

    def interface(self, model: type[BaseModel], *, optional_defaults: bool = False) -> str:
        """One model as an exported interface, docstring and field notes included."""
        lines = [_tsdoc(model.__doc__), f"export interface {model.__name__} {{"]
        for name, field in model.model_fields.items():
            note = field.description or FIELD_NOTES.get((model.__name__, name))
            if note:
                lines.append(_tsdoc(note, indent="  "))
            optional = "?" if optional_defaults and not field.is_required() else ""
            wire = field.alias or name
            lines.append(f"  {wire}{optional}: {self.type_of(field.annotation)};")
        lines.append("}")
        return "\n".join(line for line in lines if line)

    def enum(self, enum_cls: type[Enum], const: str) -> str:
        """One enum as a frozen const array and the union type derived from it.

        Both, rather than a bare union, because the console needs the values at
        runtime to build a filter and the type at compile time to check it —
        and deriving the type from the array is what keeps them identical.
        """
        self._enums.add(enum_cls)
        members = ",\n".join(f"  {_quote(str(member.value))}" for member in enum_cls)
        return "\n".join(
            part
            for part in (
                _tsdoc(enum_cls.__doc__),
                f"export const {const} = [\n{members},\n] as const;",
                f"export type {enum_cls.__name__} = (typeof {const})[number];",
            )
            if part
        )


# ── sections ─────────────────────────────────────────────────────────────────


def _header() -> str:
    return dedent(
        """\
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
         */"""
    )


def _policy_section() -> str:
    """The routing table, the approver map, every threshold, and one helper."""
    fixed = "\n".join(
        f"  {action.value}: {_quote(route.value)},"
        for action in Action
        for route in (FIXED_ROUTES.get(action),)
        if route is not None
    )
    approvers = "\n".join(
        f"  {route.value}: [{', '.join(_quote(role.value) for role in APPROVERS[route])}],"
        for route in Route
    )
    # Iterated over ``Action`` rather than over ``AUTO_ACTIONS`` itself: that is
    # a frozenset of enum members, whose iteration order varies between
    # processes, and an emitted file that differs run to run would fail --check
    # for no reason. Declaration order is also policy order.
    auto = "\n".join(f"  {_quote(action.value)}," for action in Action if action in AUTO_ACTIONS)
    thresholds = "\n".join(
        f"export const {field.name.upper()} = "
        f"{_number(getattr(DEFAULT_POLICY_CONFIG, field.name))};"
        for field in dataclass_fields(PolicyConfig)
    )
    tools = ",\n".join(f"  {_quote(name)}" for name in TOOL_NAMES)
    return "\n".join(
        (
            "/** The auto/L1/L2 table. BLOCK_CARD is absent because it is the only",
            " *  exposure-dependent action in the policy: L1 at exposure <= the L2",
            " *  threshold (inclusive), L2 above it. */",
            f"export const FIXED_ROUTES: Partial<Record<Action, Route>> = {{\n{fixed}\n}};",
            "",
            "/** Actions that are unconditionally `auto`. Ten of the fourteen. */",
            f"export const AUTO_ACTIONS: Action[] = [\n{auto}\n];",
            "",
            "/** Which roles may approve which route. */",
            f"export const APPROVERS: Record<Route, Role[]> = {{\n{approvers}\n}};",
            "",
            "/** Every tunable number in the Fraud Policy, from PolicyConfig. */",
            thresholds,
            "",
            "/** Total log-odds any one evidence group may contribute, in either direction. */",
            f"export const GROUP_CAP = {_number(GROUP_CAP)};",
            "",
            "/** The installed graph queries. A tool name in a ref is one of these. */",
            f"export const TOOL_NAMES = [\n{tools},\n] as const;",
            "export type ToolName = (typeof TOOL_NAMES)[number];",
            "",
            "/** Display only. The route that binds is always the one the server recomputed. */",
            "export function routeFor(action: Action, exposureUsd: number): Route {",
            "  const fixed = FIXED_ROUTES[action];",
            "  if (fixed) return fixed;",
            '  if (action === "BLOCK_CARD")',
            '    return exposureUsd <= BLOCK_CARD_L2_THRESHOLD ? "L1" : "L2";',
            "  throw new Error(`no route defined for ${action}`);",
            "}",
        )
    )


def _stream_section(emitter: TypeScriptEmitter) -> str:
    """The event union, the envelope, every payload and the narrowing helper."""
    event_union = "\n".join(f"  | {_quote(name)}" for name in EVENT_TYPES)
    terminal = ", ".join(_quote(name) for name in EVENT_TYPES if name in TERMINAL_EVENTS)
    envelope = dedent(
        f"""\
        export interface SseEnvelope<T extends SentinelEventType, P> {{
          v: {ENVELOPE_VERSION};
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
        }}"""
    )
    payloads: list[str] = []
    seen: set[str] = set()
    for name in EVENT_TYPES:
        model = EVENT_PAYLOADS[name]
        if model.__name__ in seen or model in EVENT_VALUE_MODELS:
            continue
        seen.add(model.__name__)
        payloads.append(emitter.interface(model))
    union = "\n".join(
        f"  | SseEnvelope<{_quote(name)}, {EVENT_PAYLOADS[name].__name__}>" for name in EVENT_TYPES
    )
    payload_map = "\n".join(
        f"  {_quote(name)}: {EVENT_PAYLOADS[name].__name__};" for name in EVENT_TYPES
    )
    return "\n\n".join(
        (
            f"export type SentinelEventType =\n{event_union};",
            "export const SENTINEL_EVENT_TYPES: SentinelEventType[] = [\n"
            + "\n".join(f"  {_quote(name)}," for name in EVENT_TYPES)
            + "\n];",
            "/** After one of these the server closes the connection; do not reconnect. */\n"
            f"export const TERMINAL_EVENTS: SentinelEventType[] = [{terminal}];",
            envelope,
            "\n\n".join(payloads),
            "/** The payload that belongs to each event name. */\n"
            f"export interface EventPayloadMap {{\n{payload_map}\n}}",
            f"export type SentinelEvent =\n{union};",
            dedent(
                """\
                export const isEvent =
                  <T extends SentinelEventType>(t: T) =>
                  (e: SentinelEvent): e is Extract<SentinelEvent, { type: T }> =>
                    e.type === t;"""
            ),
        )
    )


def _page_aliases(emitter: TypeScriptEmitter) -> str:
    """``Page<T>`` and the named parametrisations declared in ``api.schemas``."""
    generic_doc = _tsdoc(Page.__doc__)
    lines = [f"{generic_doc}\nexport interface Page<T> {{\n  items: T[];\n  total: number;\n}}"]
    for name, value in vars(schemas).items():
        # Parametrising a generic model also binds it in the module under its
        # own repr — "Page[CaseSummary]" — which is not a TypeScript name. The
        # alias the module declared for it is, so only identifiers are emitted.
        if not name.isidentifier() or not isinstance(value, type):
            continue
        if not issubclass(value, BaseModel):
            continue
        generic = getattr(value, "__pydantic_generic_metadata__", None)
        if not generic or generic.get("origin") is not Page or not generic.get("args"):
            continue
        lines.append(f"export type {name} = {emitter.type_of(value)};")
    return "\n\n".join(lines)


def _section(title: str, body: str) -> str:
    """One banner comment and its block, ruled to the file's 79-column comments."""
    rule = "─" * max(3, 72 - len(title))
    return f"// ── {title} {rule}\n\n{body}"


def build() -> str:
    """The whole file, and the three completeness checks that let it be trusted."""
    emitter = TypeScriptEmitter()
    enums = "\n\n".join(emitter.enum(cls, const) for cls, const in ENUM_EXPORTS)
    answer = "\n\n".join(emitter.interface(model) for model in ANSWER_MODELS)
    values = "\n\n".join(emitter.interface(model) for model in EVENT_VALUE_MODELS)
    stream = _stream_section(emitter)
    responses = "\n\n".join(emitter.interface(model) for model in RESPONSE_MODELS)
    requests = "\n\n".join(
        emitter.interface(model, optional_defaults=True) for model in REQUEST_MODELS
    )
    pages = _page_aliases(emitter)

    emitted = {model.__name__ for model in (*ANSWER_MODELS, *EVENT_VALUE_MODELS, *RESPONSE_MODELS)}
    emitted |= {model.__name__ for model in (*REQUEST_MODELS, *EVENT_PAYLOADS.values())}
    _check_complete(emitter, emitted)

    body = "\n\n".join(
        (
            _header(),
            _section("Enums", enums),
            _section("The policy: routes, approvers, thresholds", _policy_section()),
            _section("The answer contract", answer),
            _section("Shared value objects", values),
            _section("The SSE stream", stream),
            _section("API responses", responses),
            _section("API requests and query strings", requests),
            _section("Paged responses", pages),
        )
    )
    return body + "\n"


def _check_complete(emitter: TypeScriptEmitter, emitted: set[str]) -> None:
    """Fail the emit on a gap, rather than the frontend's build on a missing type."""
    exported = {cls for cls, _ in ENUM_EXPORTS}
    missing_enums = sorted(cls.__name__ for cls in emitter.referenced_enums - exported)
    if missing_enums:
        raise RuntimeError(
            f"enums referenced by a field but not exported: {', '.join(missing_enums)}; "
            "add them to ENUM_EXPORTS"
        )
    missing_models = sorted(
        model.__name__ for model in emitter.referenced_models if model.__name__ not in emitted
    )
    if missing_models:
        raise RuntimeError(
            f"models referenced by a field but never emitted: {', '.join(missing_models)}; "
            "add them to a section list"
        )
    defined = {
        name
        for name, value in vars(schemas).items()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value.__module__ == schemas.__name__
        and not getattr(value, "__pydantic_generic_metadata__", {}).get("args")
    }
    orphans = sorted(defined - emitted - NOT_EMITTED)
    if orphans:
        raise RuntimeError(
            f"models defined in api.schemas but in no section: {', '.join(orphans)}; "
            "list them, or add them to NOT_EMITTED with a reason"
        )


# ── rendering helpers ────────────────────────────────────────────────────────


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _number(value: float | int) -> str:
    """A Python number as a TypeScript literal, floats keeping their point."""
    return repr(value)


def _tsdoc(text: str | None, indent: str = "") -> str:
    """A Python docstring as a TSDoc block, with RST roles and markup unwound."""
    if not text or not text.strip():
        return ""
    cleaned = _RST_ROLE.sub("", cleandoc(text).strip()).replace("``", "`").replace("*/", "* /")
    lines = cleaned.splitlines()
    if len(lines) == 1:
        return f"{indent}/** {lines[0]} */"
    body = "\n".join(f"{indent} * {line}".rstrip() for line in lines)
    return f"{indent}/**\n{body}\n{indent} */"


# ── command line ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.contract",
        description="Emit the TypeScript contract from the Python enums and API schemas.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    emit = sub.add_parser("emit", help="write the contract, or check the one on disk")
    emit.add_argument("--out", type=Path, required=True, help="path to contract.ts")
    emit.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit non-zero when the file on disk differs",
    )
    return parser


def cmd_emit(args: argparse.Namespace) -> int:
    out: Path = args.out
    generated = build()
    if not args.check:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generated, encoding="utf-8")
        print(f"wrote {out} ({len(generated.splitlines())} lines)")
        return EXIT_OK

    if not out.exists():
        print(f"{out} does not exist; run without --check to create it", file=sys.stderr)
        return EXIT_ERROR
    current = out.read_text(encoding="utf-8")
    if current == generated:
        print(f"{out} is up to date")
        return EXIT_OK
    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        generated.splitlines(keepends=True),
        fromfile=f"{out} (on disk)",
        tofile="generated",
        n=2,
    )
    sys.stderr.writelines(diff)
    print(
        f"\n{out} is stale; run: python -m api.contract emit --out {out}",
        file=sys.stderr,
    )
    return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {"emit": cmd_emit}
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
