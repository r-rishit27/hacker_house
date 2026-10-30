"""The narrow row the detectors read, and the source they read it from."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Txn:
    txn_id: int
    card_id: str
    customer_id: str
    ts: datetime
    amt: float
    product: str
    channel: str
    addr1: str | None = None
    p_email: str | None = None
    r_email: str | None = None
    device_key: str | None = None
    device_new: str | None = None
    proxy: str | None = None
    risk_score: float | None = None

    @property
    def online(self) -> bool:
        return self.channel == "online"

    @property
    def anon_proxy(self) -> bool:
        return bool(self.proxy) and "ANONYMOUS" in self.proxy


class TxnSource(Protocol):
    """Every query a detector may make. Each returns rows ordered by ``ts``.

    ``until`` is exclusive and is always the investigation's as-of time, so no
    detector can see the future of the case it is judging.
    """

    def card_txns(self, card_id: str, until: datetime) -> list[Txn]: ...

    def txns_by_device(self, device_key: str, since: datetime, until: datetime) -> list[Txn]: ...

    def txns_by_recipient(self, r_email: str, since: datetime, until: datetime) -> list[Txn]: ...

    def txns_in_band(self, lo: float, hi: float, since: datetime, until: datetime) -> list[Txn]: ...

    def device_card_count(self, device_key: str, until: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class TypologyHit:
    """One detector firing. ``txn_ids`` are the affected transactions on the
    investigated card; ``connected_card_ids`` are the other cards the detector
    linked through the shared thing named in ``shared``."""

    key: str
    pattern: str  # a documented Pattern value, or "undocumented"
    description: str
    txn_ids: tuple[int, ...]
    connected_card_ids: tuple[str, ...] = ()
    shared: str = ""
    strength: float = 1.0  # 0..1, how decisive the detector considers itself
    evidence: dict = field(default_factory=dict)

    @property
    def coordinated(self) -> bool:
        return len(self.connected_card_ids) >= 2


@dataclass(frozen=True, slots=True)
class Explanation:
    """The card's own history accounts for an anomaly on the flagged transaction."""

    key: str
    anomaly: str
    reason: str
    evidence_txn_ids: tuple[int, ...] = ()
