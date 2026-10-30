"""The sixteen named questions the agent can ask the graph.

A ``GraphTool`` is the seam between three different spellings of the same call:
the name and arguments an LLM plans with, the parameter names the installed GSQL
declares, and the citation an analyst reads. They are deliberately three
different dicts. ``similar_prior_cases`` takes ``pattern`` from a planner and
``pattern_in`` on the wire; ``region_cluster`` takes four datetimes on the wire
and cites two ranges; ``email_cluster`` passes a risk threshold that the
citation omits because it is not part of the question.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from sentinel.domain.errors import SentinelError
from sentinel.tools.dto import (
    AmountBandProbe,
    CardAmountStats,
    CardBaseline,
    CardCaseMemory,
    CardTestingProbe,
    CardWindow,
    CustomerCaseHistory,
    DeviceNeighbors,
    DeviceNovelty,
    EmailCluster,
    ProductNovelty,
    RecurringChargeProbe,
    RegionCluster,
    RegionNovelty,
    RingExpansion,
    SimilarPriorCases,
    TxnDetail,
    TxnSequenceContext,
    VelocityProbe,
)
from sentinel.tools.log import QueryLog, ToolResult
from sentinel.tools.refs import EvidenceRef

#: The value ``ring_expand`` is gated with when a caller does not choose one.
#: 116 of the 9,704 device profiles are generic browser fingerprints carrying
#: 24,653 of the card-to-device links, the largest spanning 842 cards. Ungated,
#: this query invents a ring on nearly every online case, so the parameter is
#: always sent — it is a correctness gate, not a tuning knob.
DEFAULT_MAX_DEVICE_CARDS = 20


class GraphQueryRunner(Protocol):
    """The slice of ``GraphRepository`` the registry needs.

    Structural, so this package does not import the graph package and the
    registry can be tested against a stub.
    """

    async def run_query(self, name: str, params: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ToolParam:
    """One argument, in its three spellings.

    ``name`` is what a planner writes, ``wire`` is the GSQL parameter, and
    ``ref_name`` is what the citation calls it.
    """

    name: str
    wire: str
    kind: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    #: Whether the argument belongs in the human-readable citation.
    in_ref: bool = True
    ref_name: str | None = None

    @property
    def json_type(self) -> str:
        return {"datetime": "string", "integer": "integer", "number": "number"}.get(
            self.kind, "string"
        )

    def coerce(self, value: Any) -> Any:
        """Narrow a planner's value to the type GSQL declares.

        Raises ``ValueError`` when the value cannot be narrowed, which the
        registry turns into a failed ``ToolResult`` rather than a crash.
        """
        if self.kind == "integer":
            return int(value)
        if self.kind == "number":
            return float(value)
        if self.kind == "datetime" and isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return str(value)


#: Builds the citation's argument dict from resolved arguments.
RefBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class GraphTool:
    """One installed GSQL query, described well enough for an LLM to choose it.

    Responsibility: translate planner arguments into wire parameters, build the
    citation, and parse the response into its DTO. It runs nothing itself.
    Collaborators: :class:`ToolRegistry` executes it against a
    ``GraphRepository``; :class:`~sentinel.tools.refs.EvidenceRef` formats the
    citation; the DTO in ``sentinel.tools.dto`` types the result.
    """

    name: str
    query_name: str
    question: str
    dto: type[BaseModel]
    params: tuple[ToolParam, ...] = ()
    #: Argument names whose values are entity ids worth carrying on the result.
    entity_params: tuple[str, ...] = ()
    ref_builder: RefBuilder | None = None

    def json_schema(self) -> dict[str, Any]:
        """An OpenAI function-calling tool definition."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in self.params:
            schema: dict[str, Any] = {"type": param.json_type}
            if param.description:
                schema["description"] = param.description
            if param.kind == "datetime":
                schema["description"] = (
                    f"{schema.get('description', '')} Format: YYYY-MM-DD HH:MM:SS.".strip()
                )
            if param.required:
                required.append(param.name)
            elif param.default is not None:
                schema["description"] = (
                    f"{schema.get('description', '')} Defaults to {param.default}.".strip()
                )
            properties[param.name] = schema
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.question,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def build_params(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Planner arguments -> GSQL wire parameters, in the declared order.

        Every declared parameter is present in the result, defaults included,
        so a query is never run half-specified.
        """
        resolved = self._resolve(args)
        return {param.wire: resolved[param.name] for param in self.params}

    def build_ref(self, args: Mapping[str, Any]) -> str:
        """Planner arguments -> the citation that will appear in the answer file."""
        resolved = self._resolve(args)
        if self.ref_builder is not None:
            return EvidenceRef.query(self.name, self.ref_builder(resolved))
        cited = {
            param.ref_name or param.name: resolved[param.name]
            for param in self.params
            if param.in_ref
        }
        return EvidenceRef.query(self.name, cited)

    def entity_ids(self, args: Mapping[str, Any]) -> list[str]:
        resolved = self._resolve(args)
        return [str(resolved[name]) for name in self.entity_params if resolved.get(name)]

    def parse(self, raw: Mapping[str, Any]) -> BaseModel:
        """Raise ``ValidationError`` rather than return a half-built DTO."""
        return self.dto.model_validate(dict(raw))

    def _resolve(self, args: Mapping[str, Any]) -> dict[str, Any]:
        declared = {param.name for param in self.params}
        unknown = sorted(set(args) - declared)
        if unknown:
            raise ValueError(
                f"{self.name}: unknown argument(s) {', '.join(unknown)}; "
                f"expected {', '.join(sorted(declared))}"
            )
        resolved: dict[str, Any] = {}
        for param in self.params:
            if param.name in args and args[param.name] is not None:
                resolved[param.name] = param.coerce(args[param.name])
            elif param.required:
                raise ValueError(f"{self.name}: missing required argument '{param.name}'")
            else:
                resolved[param.name] = param.default
        return resolved


@dataclass(frozen=True)
class ToolCall:
    """One planned invocation. ``why`` is the planner's stated reason and is
    carried into the trace, not into the query."""

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)
    why: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ToolCall:
        return cls(
            name=str(value["name"]),
            args=dict(value.get("args") or {}),
            why=str(value.get("why") or ""),
        )


class ToolRegistry:
    """The agent's hands: every graph tool, executed and logged.

    Responsibility: dispatch a named tool against the repository, time it, parse
    it, and record the result in this investigation's log. It decides nothing
    about fraud.
    Collaborators: a ``GraphRepository`` (as :class:`GraphQueryRunner`), one
    :class:`~sentinel.tools.log.QueryLog` per investigation, and the
    :class:`GraphTool` catalogue.

    Failure has two shapes and they stay distinct. An unknown tool name raises,
    because no honest ``ToolResult`` can cite a tool that does not exist and the
    planner is specified to validate names against :meth:`names` first. A call
    that was well-formed but did not answer — bad arguments, a graph error, an
    unparsable response — returns ``ToolResult(ok=False)`` and is recorded, so
    the trace shows the question that went unanswered.
    """

    def __init__(
        self,
        repo: GraphQueryRunner,
        log: QueryLog,
        tools: Sequence[GraphTool] | None = None,
    ) -> None:
        self._repo = repo
        self._log = log
        self._tools: dict[str, GraphTool] = {
            tool.name: tool for tool in (CATALOGUE if tools is None else tools)
        }

    @property
    def log(self) -> QueryLog:
        return self._log

    def names(self) -> list[str]:
        return list(self._tools)

    def catalogue(self) -> tuple[GraphTool, ...]:
        """The registered tools themselves, for a planner that needs their questions."""
        return tuple(self._tools.values())

    def get(self, name: str) -> GraphTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ValueError(
                f"unknown tool '{name}'; known tools: {', '.join(self.names())}"
            ) from None

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def describe(self) -> list[dict[str, Any]]:
        """OpenAI function-calling definitions for every registered tool."""
        return [tool.json_schema() for tool in self._tools.values()]

    async def call(self, name: str, args: Mapping[str, Any] | None = None) -> ToolResult:
        tool = self.get(name)
        args = args or {}
        try:
            wire = tool.build_params(args)
            ref = tool.build_ref(args)
            entity_ids = tool.entity_ids(args)
        except (ValueError, TypeError) as exc:
            return self._log.record(
                ToolResult.failed(
                    name, EvidenceRef.query(name, dict(args)), f"bad arguments: {exc}"
                )
            )

        started = perf_counter()
        try:
            raw = await self._repo.run_query(tool.query_name, wire)
        except SentinelError as exc:
            return self._log.record(
                ToolResult.failed(
                    name, ref, f"{exc.code}: {exc.message}", perf_counter() - started, entity_ids
                )
            )
        except TimeoutError as exc:
            return self._log.record(
                ToolResult.failed(
                    name, ref, f"timeout: {exc}", perf_counter() - started, entity_ids
                )
            )

        try:
            data = tool.parse(raw)
        except ValidationError as exc:
            return self._log.record(
                ToolResult.failed(
                    name,
                    ref,
                    f"unparsable result: {exc.error_count()} field(s) did not validate",
                    perf_counter() - started,
                    entity_ids,
                )
            )

        return self._log.record(
            ToolResult(
                tool=name,
                ref=ref,
                data=data,
                entity_ids=entity_ids,
                elapsed_s=perf_counter() - started,
                ok=True,
            )
        )

    async def call_many(self, plan: Sequence[ToolCall | Mapping[str, Any]]) -> list[ToolResult]:
        """Run independent calls concurrently; results come back in plan order.

        Names are validated up front so one typo cannot leave half a batch in
        flight. The repository's own semaphore bounds the real concurrency.
        """
        calls = [
            item if isinstance(item, ToolCall) else ToolCall.from_mapping(item) for item in plan
        ]
        unknown = sorted({call.name for call in calls if call.name not in self._tools})
        if unknown:
            raise ValueError(
                f"unknown tool(s) {', '.join(unknown)}; known tools: {', '.join(self.names())}"
            )
        return list(await asyncio.gather(*(self.call(c.name, c.args) for c in calls)))


# ── the catalogue ────────────────────────────────────────────────────────────


def _range(low: str, high: str) -> Callable[[Mapping[str, Any]], str]:
    def render(resolved: Mapping[str, Any]) -> str:
        return f"{resolved[low]}..{resolved[high]}"

    return render


def _region_cluster_ref(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """The citation names the two windows, not their four endpoints."""
    return {
        "region": resolved["region"],
        "window": _range("win_from", "win_to")(resolved),
        "baseline": _range("base_from", "base_to")(resolved),
        "risk_thresh": resolved["risk_thresh"],
    }


def _similar_prior_cases_ref(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """An empty filter means "any"; an empty string in a citation reads as a bug."""
    return {
        "pattern": resolved["pattern"] or "any",
        "exposure": _range("amt_lo", "amt_hi")(resolved),
        "outcome": resolved["outcome"] or "any",
        "k": resolved["k"],
    }


_CENTER = "The moment the window is centred on — the alert transaction's ts."

CATALOGUE: tuple[GraphTool, ...] = (
    GraphTool(
        name="txn_detail",
        query_name="txn_detail",
        question=(
            "What was flagged, on what device and on whose card. The only source of the "
            "alert transaction's true timestamp; call this first."
        ),
        dto=TxnDetail,
        params=(ToolParam("txn_id", "t_in", description="The flagged transaction id."),),
        entity_params=("txn_id",),
    ),
    GraphTool(
        name="card_baseline",
        query_name="card_baseline",
        question=(
            "What normal looks like for this cardholder: the card's own statistics, the "
            "customer, and the customer's other cards."
        ),
        dto=CardBaseline,
        params=(ToolParam("card_id", "c_in", description="The card the alert is on."),),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="card_window",
        query_name="card_window",
        question="What else happened on this card around the alert, ordered in time.",
        dto=CardWindow,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("center", "center", kind="datetime", description=_CENTER),
            ToolParam(
                "hours",
                "hours",
                kind="integer",
                description="Half-width of the window in hours.",
                required=False,
                default=72,
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="card_testing_probe",
        query_name="card_testing_probe",
        question=(
            "Small online authorisations shortly before the alert and the largest purchase "
            "after it — the card-testing shape in Rule R5."
        ),
        dto=CardTestingProbe,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("center", "center", kind="datetime", description=_CENTER),
            ToolParam(
                "small_amt",
                "small_amt",
                kind="number",
                description="What counts as a small authorisation, in USD.",
                required=False,
                default=5.0,
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="region_novelty",
        query_name="region_novelty",
        question=(
            "Is this billing region new for this card as of the alert, and was the card "
            "used in person elsewhere within a day."
        ),
        dto=RegionNovelty,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("region", "region", description="The billing region code, e.g. 330.0."),
            ToolParam(
                "as_of",
                "as_of",
                kind="datetime",
                description="Evaluate novelty as of this moment, never over the whole file.",
            ),
        ),
        entity_params=("card_id", "region"),
    ),
    GraphTool(
        name="amount_band_probe",
        query_name="amount_band_probe",
        question=(
            "How often this card has been charged near this amount before, against its own "
            "median, p95 and maximum."
        ),
        dto=AmountBandProbe,
        # Declared in GSQL order (c_in, amt, tol, as_of); the planner's natural
        # order is card, amount, moment, tolerance, and the two are not the same.
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("amt", "amt", kind="number", description="The disputed amount in USD."),
            ToolParam(
                "tol",
                "tol",
                kind="number",
                description="Half-width of the amount band in USD.",
                required=False,
                default=0.5,
            ),
            ToolParam(
                "as_of",
                "as_of",
                kind="datetime",
                description="Count only charges before this moment as prior.",
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="device_novelty",
        query_name="device_novelty",
        question=(
            "The alert's identity flags (id_15, id_23, id_34) and whether this card has "
            "used this device before. An empty device_key means no identity record exists, "
            "which is not the same as a new device."
        ),
        dto=DeviceNovelty,
        params=(ToolParam("txn_id", "t_in", description="The flagged transaction id."),),
        entity_params=("txn_id",),
    ),
    GraphTool(
        name="device_neighbors",
        query_name="device_neighbors",
        question=(
            "Which other cards used this device profile in the window, and how many cards "
            "the profile spans overall — its specificity."
        ),
        dto=DeviceNeighbors,
        params=(
            ToolParam("device_key", "d_in", description="The device profile key."),
            ToolParam("center", "center", kind="datetime", description=_CENTER),
            ToolParam(
                "days",
                "days",
                kind="integer",
                description="Half-width of the window in days.",
                required=False,
                default=30,
            ),
        ),
        entity_params=("device_key",),
    ),
    GraphTool(
        name="region_cluster",
        query_name="region_cluster",
        question=(
            "High-risk transaction rate in a billing region during the alert window, "
            "against a matched earlier baseline window. A raw count proves nothing."
        ),
        dto=RegionCluster,
        params=(
            ToolParam("region", "r_in", description="The billing region code."),
            ToolParam("win_from", "win_from", kind="datetime", description="Window start."),
            ToolParam("win_to", "win_to", kind="datetime", description="Window end."),
            ToolParam("base_from", "base_from", kind="datetime", description="Baseline start."),
            ToolParam("base_to", "base_to", kind="datetime", description="Baseline end."),
            ToolParam(
                "risk_thresh",
                "risk_thresh",
                kind="number",
                description="Model score at or above which a transaction counts as high risk.",
                required=False,
                default=0.7,
            ),
        ),
        entity_params=("region",),
        ref_builder=_region_cluster_ref,
    ),
    GraphTool(
        name="email_cluster",
        query_name="email_cluster",
        question=(
            "Transactions and high-risk transactions on a recipient email DOMAIN in the "
            "window. A base rate for the domain, never evidence of a ring on its own."
        ),
        dto=EmailCluster,
        params=(
            ToolParam("domain", "e_in", description="The email domain, e.g. gmail.com."),
            ToolParam("center", "center", kind="datetime", description=_CENTER),
            ToolParam(
                "days",
                "days",
                kind="integer",
                description="Half-width of the window in days.",
                required=False,
                default=30,
            ),
            # v1's citation omits the threshold and the answer files were graded
            # with it omitted; it is a parameter of the measurement, not the question.
            ToolParam(
                "risk_thresh",
                "risk_thresh",
                kind="number",
                description="Model score at or above which a transaction counts as high risk.",
                required=False,
                default=0.7,
                in_ref=False,
            ),
        ),
        entity_params=("domain",),
    ),
    GraphTool(
        name="ring_expand",
        query_name="ring_expand",
        question=(
            "Other cards sharing a SPECIFIC device with this card in the window. "
            "max_device_cards excludes generic browser fingerprints; without it almost any "
            "card that has ever been used online returns a phantom ring."
        ),
        dto=RingExpansion,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("center", "center", kind="datetime", description=_CENTER),
            ToolParam(
                "days",
                "days",
                kind="integer",
                description="Half-width of the window in days.",
                required=False,
                default=30,
            ),
            ToolParam(
                "max_device_cards",
                "max_device_cards",
                kind="integer",
                description=(
                    "Ignore device profiles spanning more than this many cards. "
                    "Always sent; lower it to tighten the gate, never remove it."
                ),
                required=False,
                default=DEFAULT_MAX_DEVICE_CARDS,
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="recurring_charge_probe",
        query_name="recurring_charge_probe",
        question=(
            "Whether charges near this amount recur on this card across months — the "
            "forgotten-subscription reading of a disputed charge, Rule R7."
        ),
        dto=RecurringChargeProbe,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("amt", "amt", kind="number", description="The disputed amount in USD."),
            ToolParam(
                "tol",
                "tol",
                kind="number",
                description="Half-width of the amount band in USD.",
                required=False,
                default=0.5,
                in_ref=False,
            ),
            ToolParam(
                "product",
                "product",
                description="Restrict to one product code; empty means any.",
                required=False,
                default="",
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="velocity_probe",
        query_name="velocity_probe",
        question=(
            "Count, value, peak risk, distinct regions and distinct devices on this card "
            "in the window — burst, geography and device churn together."
        ),
        dto=VelocityProbe,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("center", "center", kind="datetime", description=_CENTER),
            ToolParam(
                "hours",
                "hours",
                kind="integer",
                description="Half-width of the window in hours.",
                required=False,
                default=48,
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="customer_case_history",
        query_name="customer_case_history",
        question=(
            "This customer's closed cases, their lifetime exposure, and how often the "
            "cases they themselves reported were confirmed as fraud."
        ),
        dto=CustomerCaseHistory,
        params=(ToolParam("customer_id", "customer_id", description="The customer id."),),
        entity_params=("customer_id",),
    ),
    GraphTool(
        name="similar_prior_cases",
        query_name="similar_prior_cases",
        question=(
            "Closed cases filtered by pattern, outcome and exposure band. Returns the k "
            "CHEAPEST matches, not the k most similar — use GraphRAG for similarity."
        ),
        dto=SimilarPriorCases,
        params=(
            ToolParam(
                "pattern",
                "pattern_in",
                description="One of the five documented patterns; empty means any.",
                required=False,
                default="",
            ),
            ToolParam(
                "amt_lo",
                "amt_lo",
                kind="number",
                description="Lowest exposure in USD to include.",
                required=False,
                default=0.0,
            ),
            ToolParam(
                "amt_hi",
                "amt_hi",
                kind="number",
                description="Highest exposure in USD to include.",
                required=False,
                default=1e9,
            ),
            ToolParam(
                "outcome",
                "outcome_in",
                description="confirmed_fraud or cleared; empty means any.",
                required=False,
                default="",
            ),
            ToolParam(
                "k",
                "k",
                kind="integer",
                description="How many cases to return.",
                required=False,
                default=10,
            ),
        ),
        ref_builder=_similar_prior_cases_ref,
    ),
    GraphTool(
        name="case_memory_for_card",
        query_name="case_memory_for_card",
        question=(
            "Everything already known about this card: the bank's closed cases and the "
            "cases Sentinel itself has written."
        ),
        dto=CardCaseMemory,
        params=(ToolParam("card_id", "c_in", description="The card the alert is on."),),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="txn_sequence_context",
        query_name="txn_sequence_context",
        question=(
            "How long before this transaction the previous one on the same card was, and "
            "how long until the next. Answers whether it sits inside a burst."
        ),
        dto=TxnSequenceContext,
        params=(ToolParam("txn_id", "t_in", description="The flagged transaction."),),
        entity_params=("txn_id",),
    ),
    GraphTool(
        name="product_novelty",
        query_name="product_novelty",
        question="Has this card ever been used for this product code before the alert.",
        dto=ProductNovelty,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam("product", "product", description="The ProductCD: W, C, R, H or S."),
            ToolParam(
                "as_of",
                "as_of",
                kind="datetime",
                description="Count only purchases before this moment as prior.",
            ),
        ),
        entity_params=("card_id",),
    ),
    GraphTool(
        name="card_amount_stats",
        query_name="card_amount_stats",
        question=(
            "This card's own amount distribution before the alert: how many prior charges, "
            "and the sums from which their mean and spread follow."
        ),
        dto=CardAmountStats,
        params=(
            ToolParam("card_id", "c_in", description="The card the alert is on."),
            ToolParam(
                "as_of",
                "as_of",
                kind="datetime",
                description="Summarise only the history before this moment.",
            ),
        ),
        entity_params=("card_id",),
    ),
)

#: The nineteen installed queries the agent may call, in the order they appear
#: in the GSQL files. The read-surface queries the console uses — alerts_queue,
#: case_by_id, case_subgraph — are deliberately NOT here: they answer the UI's
#: questions, not the investigation's, and an agent given them would waste calls.
TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in CATALOGUE)
