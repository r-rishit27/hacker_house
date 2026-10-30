"""Looking for rings nobody asked about.

The twenty benchmark alerts are each about one card. A ring is not: it is a set
of cards that only becomes visible when you stop investigating cards one at a
time. Guide.md says so outright — "Some cases can only be solved by asking what
happened on *other* cards" — and names the undocumented pattern as explicitly
scored.

The method is deliberately the boring one, because the interesting one is wrong
here. One hop out from each seed card through device profiles, **gated on
profile specificity**, then connected components over the card-to-card
co-occurrence graph, computed client-side. No community detection, no
modularity: a clustering algorithm would only add a parameter to defend.

The gate is the whole analysis. 116 of the 9,704 device profiles carry 24,653
of the card links and the largest spans 842 cards; ungated, every card that has
ever been used in a browser lands in one giant component and the output says
nothing. Gated at twenty cards per profile, what survives is cards that share a
fingerprint specific enough to mean something.

**And at two hops the gate is not enough.** Two hops from the twenty seed
cards, with every profile gated the same way, reaches a single connected
component of well over a thousand cards — the exact figure is `two_hop_reach`
in the report, because it moves with the window. That is not a ring, it is the
observation that this device graph has a giant component: a card shares a
specific profile with a handful of others, each of which shares a *different*
specific profile with a handful more, and two hops joins most of the graph.

So the ring test runs at one hop, which is what R6 is about — "several cards
show fraud from the same device profile" — and the two-hop reach is reported
as the measurement that rules the second hop out, rather than quietly dropped.

Output goes to ``exploration/``, never to ``cases/``. None of this is part of
the graded answer for any of the twenty; it is the question the twenty do not
ask.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sentinel.domain.alert import Alert
from sentinel.graph.normalize import ResponseNormalizer
from sentinel.graph.repository import GraphRepository

logger = logging.getLogger(__name__)

#: A profile on more cards than this is a browser configuration, not a device.
#: Same constant the investigation uses, for the same reason.
MAX_DEVICE_CARDS = 20

#: How far either side of the seed transaction to look.
WINDOW_DAYS = 30

#: Cards per hop. The query caps its own result; this is the argument.
MAX_CARDS_PER_HOP = 40

#: A component of two cards is a coincidence. Three is a question.
MIN_COMPONENT_CARDS = 3

#: Above this a component is not a ring, it is a neighbourhood. A shared
#: fingerprint that forty cards have is a fingerprint class the gate did not
#: catch, and calling it a ring would be the phantom-ring failure with an
#: extra step.
MAX_RING_CARDS = 40

#: How many cards to name before saying "and N more". A ring worth reading is
#: small; a list of 1,339 ids is a way of not saying anything.
NAMED_CARDS = 25


@dataclass
class Component:
    """One connected set of cards, and what makes it interesting."""

    cards: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    customers: set[str] = field(default_factory=set)
    #: Cards in this component that appear in a confirmed-fraud closed case.
    confirmed_fraud_cards: list[str] = field(default_factory=list)
    exposure_usd: float = 0.0

    @property
    def size(self) -> int:
        return len(self.cards)

    @property
    def spans_customers(self) -> int:
        return len(self.customers)

    @property
    def is_ring(self) -> bool:
        """Small, multi-customer, and already known to the bank as fraud.

        All three halves matter. One customer with three cards on one laptop
        is a household. Two customers sharing a fingerprint with no confirmed
        fraud between them is a coffee shop. And a component of four hundred
        cards is a fingerprint class the gate did not catch — calling that a
        ring is the phantom-ring failure with an extra step.
        """
        return (
            MIN_COMPONENT_CARDS <= self.size <= MAX_RING_CARDS
            and self.spans_customers >= 2
            and len(self.confirmed_fraud_cards) >= 2
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "cards": sorted(self.cards),
            "size": self.size,
            "devices": sorted(self.devices),
            "seeds": sorted(self.seeds),
            "customers": sorted(self.customers),
            "spans_customers": self.spans_customers,
            "confirmed_fraud_cards": sorted(self.confirmed_fraud_cards),
            "exposure_usd": round(self.exposure_usd, 2),
            "is_ring": self.is_ring,
        }


#: How much of the examined neighbourhood is written out. The whole two-hop
#: set is 1,385 cards, which is the point being made and not a drawing; these
#: caps keep the artefact readable and the canvas legible while still showing
#: the shape.
EXAMINED_DEVICES = 48
EXAMINED_CARDS = 20


@dataclass
class DeviceLink:
    """One device profile and the cards seen on it, inside the window.

    Recorded whether or not it goes on to form a component. A sweep that
    reports only what passed its gates cannot be checked: "no ring" and
    "nothing was looked at" produce the same empty file. This is the looking.
    """

    device: str
    cards: list[str]
    seeds: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"device": self.device, "cards": self.cards, "seeds": self.seeds}


@dataclass
class RingReport:
    """Everything the sweep found, and everything it deliberately did not look at."""

    seeds: int
    components: list[Component]
    generic_profiles_skipped: int
    #: Every device the sweep examined, with the cards on it. The evidence for
    #: the negative result.
    examined: list[DeviceLink] = field(default_factory=list)
    #: The most interesting thing the sweep saw that is *not* a ring: the
    #: fingerprint shared by the most customers at two hops. Annotated like a
    #: component so it can be read the same way, and reported separately so it
    #: can never be counted as one.
    focus: Component | None = None
    #: Cards reachable in *two* hops from the seeds, as one connected set. The
    #: number that rules two-hop co-occurrence out as evidence of a ring.
    two_hop_reach: int = 0
    two_hop_customers: int = 0
    window_days: int = WINDOW_DAYS
    max_device_cards: int = MAX_DEVICE_CARDS

    @property
    def rings(self) -> list[Component]:
        return [c for c in self.components if c.is_ring]

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": {
                "hops": 1,
                "window_days": self.window_days,
                "max_device_cards": self.max_device_cards,
                "min_component_cards": MIN_COMPONENT_CARDS,
                "max_ring_cards": MAX_RING_CARDS,
                "note": (
                    "Device profiles above max_device_cards are browser configurations "
                    "rather than devices and are excluded. The ring test runs at one hop: "
                    "two hops reaches a single giant component, which is reported as "
                    "two_hop_reach rather than as a ring."
                ),
            },
            "seeds": self.seeds,
            "generic_profiles_skipped": self.generic_profiles_skipped,
            "two_hop_reach": self.two_hop_reach,
            "two_hop_customers": self.two_hop_customers,
            "components_found": len(self.components),
            "rings_found": len(self.rings),
            "components": [c.as_dict() for c in self.components],
            "examined": [link.as_dict() for link in self.examined],
            "focus": self.focus.as_dict() if self.focus is not None else None,
        }

    def as_markdown(self) -> str:
        lines = [
            "# Ring discovery",
            "",
            "## Method",
            "",
            f"One hop from {self.seeds} seed cards through device profiles gated at "
            f"{self.max_device_cards} cards, a {self.window_days}-day window either side, then "
            "connected components client-side. A component is a ring only if it holds "
            f"{MIN_COMPONENT_CARDS} to {MAX_RING_CARDS} cards across two or more customers with two "
            "or more of them already confirmed fraudulent by the bank.",
            "",
            "## Why one hop and not two",
            "",
            f"Two hops from the same seeds, with every profile gated the same way, reaches "
            f"**{self.two_hop_reach:,} cards across {self.two_hop_customers:,} customers** as one "
            "connected set. That is not twenty rings, it is the observation that this device "
            "graph has a giant component: a card shares a specific fingerprint with a handful of "
            "others, each of those shares a different specific fingerprint with a handful more, "
            "and two hops is enough to join most of the graph. Two-hop co-occurrence is therefore "
            "not evidence of anything, and the gate that makes one hop meaningful does not "
            "survive a second.",
            "",
            "This is the same failure the specificity gate exists for, one hop further out. 116 "
            "of the 9,704 profiles carry 24,653 of the card links and the largest spans 842 "
            "cards; gating at twenty fixes the first hop and not the second.",
            "",
            "## What one hop found",
            "",
        ]
        if not self.components:
            lines += [
                f"**No component of {MIN_COMPONENT_CARDS} or more cards.** That is a finding, not "
                "a gap: with the specificity gate applied, the twenty benchmark cards do not sit "
                "in a shared-device ring, and R6 is correct not to fire on them.",
            ]
            return "\n".join(lines)

        rings = self.rings
        lines += [
            f"**{len(self.components)} component(s)** of {MIN_COMPONENT_CARDS} or more cards; "
            f"**{len(rings)}** meet the ring test.",
            "",
            "| cards | customers | confirmed fraud | exposure | ring | reached from |",
            "|---|---|---|---|---|---|",
        ]
        for component in sorted(self.components, key=lambda c: -c.size):
            lines.append(
                f"| {component.size} | {component.spans_customers} | "
                f"{len(component.confirmed_fraud_cards)} | "
                f"${component.exposure_usd:,.2f} | "
                f"{'**yes**' if component.is_ring else 'no'} | "
                f"{', '.join(sorted(component.seeds)[:4])} |"
            )
        for component in sorted(rings, key=lambda c: -c.size):
            lines += [
                "",
                f"### Ring of {component.size} cards across {component.spans_customers} customers",
                "",
                f"- **Cards:** {_names(component.cards)}",
                f"- **Shared profiles:** {_names(component.devices, 6)}",
                f"- **Already confirmed fraudulent:** {_names(component.confirmed_fraud_cards)}",
                f"- **Exposure across those cases:** ${component.exposure_usd:,.2f}",
                f"- **Reached from:** {', '.join(sorted(component.seeds))}",
            ]
        return "\n".join(lines)


@dataclass
class RingExplorer:
    """Two-hop device expansion from a set of seed cards, then components.

    Responsibility: the sweep and the component arithmetic. It posts no
    evidence, writes no case and touches no answer file.
    Collaborators: ``GraphRepository`` for ``ring_expand_2hop`` and
    ``case_memory_for_card``; ``ResponseNormalizer`` for the wire format.
    """

    graph: GraphRepository
    normalizer: ResponseNormalizer = field(default_factory=ResponseNormalizer)
    max_device_cards: int = MAX_DEVICE_CARDS
    window_days: int = WINDOW_DAYS

    async def explore(self, alerts: Sequence[Alert]) -> RingReport:
        """One hop for the rings, two hops for the measurement that bounds them."""
        edges: list[tuple[str, str]] = []
        card_device: dict[str, set[str]] = {}
        seed_of: dict[str, set[str]] = {}
        generic_skipped = 0
        two_hop_cards: set[str] = set()
        two_hop_links: dict[str, set[str]] = {}

        for alert in alerts:
            one_hop = await self._one_hop(alert)
            if one_hop is not None:
                shared = self._link(one_hop, alert, card_device, seed_of)
                generic_skipped += _generic_count(one_hop, self.max_device_cards)
                edges += shared
            # Two hops is not used for the ring test — it reaches most of the
            # graph — but the size it reaches is the reason, and a reason
            # needs a number.
            two_hop = await self._two_hop(alert)
            if two_hop is not None:
                for card, device in _pairs(two_hop.get("card_device_pairs")):
                    two_hop_cards.add(card)
                    two_hop_links.setdefault(device, set()).add(card)

        # What the sweep actually looked at, so the negative result can be
        # inspected rather than taken on trust.
        #
        # One hop shares nothing on this pack — `card_device` is empty, which
        # is the finding. So the neighbourhood recorded here is the *second*
        # hop: the thing that does connect, and the reason it is not evidence.
        # It is the picture worth drawing, because "1,385 cards" is a number
        # and a giant component is a shape.
        by_device: dict[str, set[str]] = {
            device: set(cards) for device, cards in two_hop_links.items()
        }
        for card, devices in card_device.items():
            for device in devices:
                by_device.setdefault(device, set()).add(card)
        examined = [
            DeviceLink(
                device=device,
                cards=sorted(cards)[:EXAMINED_CARDS],
                seeds=sorted({seed for card in cards for seed in seed_of.get(card, ())}),
            )
            for device, cards in sorted(
                by_device.items(), key=lambda item: (-len(item[1]), item[0])
            )[:EXAMINED_DEVICES]
        ]

        focus = _pick_focus(two_hop_links, seed_of)
        if focus is not None:
            await self._annotate(focus)

        components = _components(edges)
        out: list[Component] = []
        for cards in components:
            if len(cards) < MIN_COMPONENT_CARDS:
                continue
            component = Component(
                cards=sorted(cards),
                devices=sorted({d for c in cards for d in card_device.get(c, ())}),
                seeds=sorted({s for c in cards for s in seed_of.get(c, ())}),
            )
            await self._annotate(component)
            out.append(component)

        return RingReport(
            seeds=len(alerts),
            components=out,
            examined=examined,
            focus=focus,
            generic_profiles_skipped=generic_skipped,
            two_hop_reach=len(two_hop_cards),
            two_hop_customers=len({_customer_of(card) for card in two_hop_cards}),
            window_days=self.window_days,
            max_device_cards=self.max_device_cards,
        )

    @staticmethod
    def _link(
        rows: Mapping[str, Any],
        alert: Alert,
        card_device: dict[str, set[str]],
        seed_of: dict[str, set[str]],
    ) -> list[tuple[str, str]]:
        """Edges between every pair of cards that shared one specific profile."""
        by_device: dict[str, set[str]] = {}
        for row in rows.get("connected_cards") or ():
            attrs = row.get("attributes", row) if isinstance(row, Mapping) else {}
            card = str(attrs.get("card_id") or "").strip()
            if not card:
                continue
            for device in attrs.get("@via_device") or attrs.get("via_device") or ():
                by_device.setdefault(str(device), set()).add(card)
                card_device.setdefault(card, set()).add(str(device))
                seed_of.setdefault(card, set()).add(alert.card_id)
        # The seed belongs to every profile it reached the others through.
        for device, cards in by_device.items():
            cards.add(alert.card_id)
            card_device.setdefault(alert.card_id, set()).add(device)
        seed_of.setdefault(alert.card_id, set()).add(alert.card_id)

        edges: list[tuple[str, str]] = []
        for cards in by_device.values():
            ordered = sorted(cards)
            edges += [(ordered[0], other) for other in ordered[1:]]
        logger.info(
            "%s: %d card(s) share %d specific profile(s)",
            alert.alert_id,
            len({c for cards in by_device.values() for c in cards}) - 1,
            len(by_device),
        )
        return edges

    async def _one_hop(self, alert: Alert) -> dict[str, Any] | None:
        """`ring_expand` — cards sharing one *specific* profile with the seed."""
        return await self._query(
            "ring_expand",
            alert,
            {
                "c_in": alert.card_id,
                "center": self._center(alert),
                "days": self.window_days,
                "max_device_cards": self.max_device_cards,
            },
        )

    async def _two_hop(self, alert: Alert) -> dict[str, Any] | None:
        return await self._query(
            "ring_expand_2hop",
            alert,
            {
                "c_in": alert.card_id,
                "center": self._center(alert),
                "days": self.window_days,
                "max_device_cards": self.max_device_cards,
                "max_cards": MAX_CARDS_PER_HOP,
            },
        )

    def _center(self, alert: Alert) -> str:
        return (alert.opened_at - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    async def _query(
        self, name: str, alert: Alert, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            return await self.graph.run_query(name, params)
        except Exception as exc:  # noqa: BLE001 - one failed seed must not end the sweep
            logger.warning("%s failed on %s: %s", name, alert.card_id, exc)
            return None

    async def _annotate(self, component: Component) -> None:
        """Ask the graph what the bank already knows about these cards."""
        for card in component.cards:
            component.customers.add(_customer_of(card))
            try:
                payload = await self.graph.run_query("case_memory_for_card", {"c_in": card})
            except Exception as exc:  # noqa: BLE001 - annotation is best effort
                logger.debug("case_memory_for_card failed on %s: %s", card, exc)
                continue
            for row in self.normalizer.rows(payload.get("bank_closed_cases")):
                if str(row.get("outcome") or "") != "confirmed_fraud":
                    continue
                if card not in component.confirmed_fraud_cards:
                    component.confirmed_fraud_cards.append(card)
                component.exposure_usd += float(row.get("exposure_usd") or 0.0)


# ── plain functions ──────────────────────────────────────────────────────────


#: The scale a person can read. A fingerprint on four to a dozen cards is a
#: picture; one on two is noise and one on forty is a fingerprint class.
FOCUS_MIN_CARDS = 4
FOCUS_MAX_CARDS = 12


def _pick_focus(
    two_hop_links: Mapping[str, set[str]], seed_of: Mapping[str, set[str]]
) -> Component | None:
    """The single fingerprint worth drawing, or None.

    Chosen by how many distinct *customers* it spans rather than how many
    cards: six cards on one customer's profile is a household and says
    nothing, while six cards across five customers is the shape everybody
    opens this screen hoping to see — even when, as here, it sits at two hops
    and so cannot clear R6.

    Deterministic: ties break on card count, then on the profile id.
    """
    best: tuple[int, int, str] | None = None
    chosen: str | None = None
    for device, cards in two_hop_links.items():
        if not FOCUS_MIN_CARDS <= len(cards) <= FOCUS_MAX_CARDS:
            continue
        customers = len({_customer_of(card) for card in cards})
        key = (customers, len(cards), device)
        if best is None or key > best:
            best = key
            chosen = device
    if chosen is None:
        return None
    members = sorted(two_hop_links[chosen])
    return Component(
        cards=members,
        devices=[chosen],
        seeds=sorted({seed for card in members for seed in seed_of.get(card, ())}),
    )


def _names(values: Sequence[str], limit: int = NAMED_CARDS) -> str:
    """A readable list. A ring worth reading is short; 1,339 ids say nothing."""
    ordered = sorted(values)
    if len(ordered) <= limit:
        return ", ".join(ordered) or "none"
    return f"{', '.join(ordered[:limit])} and {len(ordered) - limit} more"


def _customer_of(card_id: str) -> str:
    """``C08623-K2`` -> ``C08623``. The dataset's own convention."""
    return card_id.split("-", 1)[0]


def _pairs(value: Any) -> list[tuple[str, str]]:
    """``["C1-K1|dev-a", ...]`` as (card, device) tuples."""
    out: list[tuple[str, str]] = []
    for item in value or ():
        if not isinstance(item, str) or "|" not in item:
            continue
        card, _, device = item.partition("|")
        if card and device:
            out.append((card, device))
    return out


def _generic_count(rows: Mapping[str, Any], gate: int) -> int:
    """How many profiles the gate excluded, as the query reports them."""
    seen = 0
    for key in ("seed_devices", "hop1_devices"):
        for row in rows.get(key) or ():
            if isinstance(row, Mapping):
                attrs = row.get("attributes", row)
                n_cards = attrs.get("n_cards") if isinstance(attrs, Mapping) else None
                if isinstance(n_cards, (int, float)) and n_cards > gate:
                    seen += 1
    return seen


def _components(edges: Iterable[tuple[str, str]]) -> list[set[str]]:
    """Connected components by union-find. Small graphs, no dependency."""
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in edges:
        a, b = find(left), find(right)
        if a != b:
            parent[a] = b

    groups: dict[str, set[str]] = {}
    for node in list(parent):
        groups.setdefault(find(node), set()).add(node)
    return list(groups.values())


def write_report(report: RingReport, directory: Path, now: datetime) -> tuple[Path, Path]:
    """``exploration/rings.json`` and ``exploration/rings.md``."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = report.as_dict()
    payload["generated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    json_path = directory / "rings.json"
    md_path = directory / "rings.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(report.as_markdown() + "\n", encoding="utf-8")
    return json_path, md_path
