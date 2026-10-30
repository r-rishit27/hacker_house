"""The case as a picture: the subgraph a `FraudCase` vertex sits in.

This endpoint is the one place the console can show that the investigation
really did happen in a graph. It reads `case_subgraph`, which returns the
transactions, cards, devices, regions and customers the case touches, and
shapes them into nodes and edges a canvas can lay out.

Two rules keep it honest. The node budget is enforced server-side and the
response says when it bit, because a canvas that silently drew half a ring
would be worse than one that said "showing 80 of 214". And generic device
profiles are **labelled as generic rather than dropped**: 116 of the 9,704
profiles carry 24,653 of the card links, and a canvas that hid them would hide
the single biggest reason a connection in this dataset means nothing.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from fastapi import Query

from api.controller import ApiController, route
from api.errors import not_found
from api.schemas import (
    GraphCanvas,
    GraphEdge,
    GraphLegendEntry,
    GraphNode,
    GraphNodeKind,
    RingComponent,
    RingDeviceLink,
    RingReport,
)
from sentinel.graph.normalize import ResponseNormalizer
from sentinel.memory.store import graph_case_id

#: A profile on more cards than this is a browser configuration. Same number
#: the investigation gates on, for the same reason.
GENERIC_DEVICE_CARDS = 20


class GraphController(ApiController):
    """The case subgraph, shaped for a canvas.

    Responsibility: one query, one projection, one honest truncation count. It
    computes nothing about fraud.
    Collaborators: ``case_subgraph`` and ``ring_expand_2hop`` in GSQL;
    ``ResponseNormalizer`` for the wire format.
    """

    prefix = "/graph"
    tags: ClassVar[list[str]] = ["graph"]

    def __init__(self, handle: Any) -> None:
        super().__init__(handle)
        self._norm = ResponseNormalizer()

    def routes(self) -> list[dict[str, Any]]:
        return [
            route(
                "/cases/{case_id}",
                self.canvas,
                summary="Nodes and edges around this case's FraudCase vertex.",
                errors={
                    404: "No case vertex has been written for this case.",
                    503: "The graph is unreachable or waking.",
                },
            ),
            route(
                "/rings",
                self.rings,
                summary="The ring sweep as `explore rings` last wrote it.",
            ),
        ]

    async def rings(self) -> RingReport:
        """Serve `exploration/rings.json`, or say plainly that it is not there.

        Never recomputed here. The sweep walks every seed's device
        neighbourhood and that is a few hundred graph calls; a page load is
        not the place to spend them, and an artefact both the CLI and the
        console read cannot drift between them.
        """
        path = self.container.settings.exploration_dir / "rings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return RingReport(available=False)
        if not isinstance(raw, dict):
            return RingReport(available=False)
        method = raw.get("method") or {}
        return RingReport(
            available=True,
            generated_at=str(raw.get("generated_at", "")),
            hops=int(method.get("hops", 1)),
            window_days=int(method.get("window_days", 30)),
            max_device_cards=int(method.get("max_device_cards", 20)),
            seeds=int(raw.get("seeds", 0)),
            generic_profiles_skipped=int(raw.get("generic_profiles_skipped", 0)),
            two_hop_reach=int(raw.get("two_hop_reach", 0)),
            two_hop_customers=int(raw.get("two_hop_customers", 0)),
            components_found=int(raw.get("components_found", 0)),
            rings_found=int(raw.get("rings_found", 0)),
            components=[
                RingComponent.model_validate(component)
                for component in raw.get("components") or []
                if isinstance(component, dict)
            ],
            focus=(
                RingComponent.model_validate(raw["focus"])
                if isinstance(raw.get("focus"), dict)
                else None
            ),
            examined=[
                RingDeviceLink.model_validate(link)
                for link in raw.get("examined") or []
                if isinstance(link, dict)
            ],
            note=str(method.get("note", "")),
        )

    async def canvas(
        self,
        case_id: str,
        max_nodes: int = Query(default=120, ge=10, le=500),
        include_ring: bool = False,
    ) -> GraphCanvas:
        vertex = graph_case_id(case_id)
        payload = await self.container.graph.run_query(
            "case_subgraph", {"f_in": vertex, "max_txns": max_nodes}
        )
        nodes: dict[str, GraphNode] = {
            vertex: GraphNode(id=vertex, kind=GraphNodeKind.FRAUD_CASE, label=case_id, focus=True)
        }
        edges: list[GraphEdge] = []
        total = 0

        for key, kind, edge_type, label_field in _PROJECTIONS:
            for row in self._norm.rows(payload.get(key)):
                total += 1
                node = _node(row, kind, label_field)
                if node is None:
                    continue
                if len(nodes) < max_nodes:
                    nodes.setdefault(node.id, node)
                    edges.append(
                        GraphEdge(
                            id=f"{edge_type}:{vertex}->{node.id}",
                            source=vertex,
                            target=node.id,
                            type=edge_type,
                            label=edge_type.replace("_", " ").lower(),
                        )
                    )

        if len(nodes) <= 1:
            raise not_found("case vertex", vertex)

        return GraphCanvas(
            case_id=case_id,
            nodes=list(nodes.values()),
            edges=edges,
            truncated=total + 1 > len(nodes),
            shown=len(nodes),
            total=total + 1,
            legend=_legend(nodes.values()),
        )


#: ``(printed key, node kind, edge type, the attribute to label the node with)``.
#: The keys are the ``PRINT ... AS`` names in ``case_subgraph``.
_PROJECTIONS: tuple[tuple[str, GraphNodeKind, str, str], ...] = (
    ("txns", GraphNodeKind.TRANSACTION, "CASE_INVOLVES", "txn_id"),
    ("cards", GraphNodeKind.CARD, "CASE_ON_CARD", "card_id"),
    ("connected_cards", GraphNodeKind.CARD, "CASE_CONNECTED_TO", "card_id"),
    ("devices", GraphNodeKind.DEVICE_PROFILE, "IMPLICATES", "label"),
    ("regions", GraphNodeKind.BILLING_REGION, "BILLED_IN", "addr1"),
    ("customers", GraphNodeKind.CUSTOMER, "OWNS", "customer_id"),
)


def _node(row: dict[str, Any], kind: GraphNodeKind, label_field: str) -> GraphNode | None:
    """One printed vertex as a canvas node, or None when it has no id."""
    identity = str(row.get(label_field) or row.get("v_id") or "").strip()
    if not identity:
        return None
    cards = row.get("n_cards")
    return GraphNode(
        id=f"{kind.value}:{identity}",
        kind=kind,
        label=identity,
        risk_score=_number(row.get("risk_score")),
        amount_usd=_number(row.get("amt")),
        n_cards=int(cards) if isinstance(cards, (int, float)) else None,
        attrs={k: v for k, v in row.items() if isinstance(v, (str, int, float, bool))},
    )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _legend(nodes: Any) -> list[GraphLegendEntry]:
    """One entry per node kind present, with how many of it there are.

    The device entry carries the number that matters: a profile spanning more
    than twenty cards is a browser configuration, not a device, and the canvas
    fades it rather than hiding it — 116 of the 9,704 profiles carry 24,653 of
    the card links, and that weakness should be visible, not tidied away.
    """
    counts: dict[GraphNodeKind, int] = {}
    for node in nodes:
        counts[node.kind] = counts.get(node.kind, 0) + 1
    return [
        GraphLegendEntry(
            kind=kind,
            label=(
                f"device profile (faded above {GENERIC_DEVICE_CARDS} cards: a browser "
                "configuration, not a device)"
                if kind is GraphNodeKind.DEVICE_PROFILE
                else kind.value
            ),
            count=count,
        )
        for kind, count in sorted(counts.items(), key=lambda item: item[0].value)
    ]
