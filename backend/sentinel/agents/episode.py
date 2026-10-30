"""Which transactions form the fraud episode, and what it exposed.

This is deterministic on purpose. ``exposure_usd`` decides two graded things —
whether ``BLOCK_CARD`` routes L1 or L2 at the $2,500 boundary, and whether the
SAR's $1,000 condition holds — and it is re-verified against the live graph to
two cents by the validator. One hallucinated transaction moves both. So the
model names the pattern; this code decides the episode.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sentinel.domain.enums import Pattern, Verdict
from sentinel.tools.dto import CardTestingProbe, CardWindow, RegionNovelty, TransactionRow

#: How far either side of the alert a card-not-present episode may reach. The
#: brief describes CNP fraud as "a burst of two to four within 48 hours".
CNP_WINDOW_HOURS = 48

#: A transaction joins a CNP episode only if it carries an incriminating signal
#: of its own; proximity in time is not evidence.
CNP_RISK_FLOOR = 0.70


@dataclass(frozen=True, slots=True)
class EpisodeScope:
    """The episode, ready to become answer fields."""

    affected_txn_ids: tuple[str, ...]
    first_suspicious_txn_id: str
    exposure_usd: float
    basis: str

    @property
    def is_empty(self) -> bool:
        return not self.affected_txn_ids


@dataclass
class EpisodeScoper:
    """Decides the fraud episode from tool results and the named pattern.

    Responsibility: ``affected_txn_ids``, ``first_suspicious_txn_id`` and
    ``exposure_usd``, plus the one-line basis that explains the choice. Pure: no
    I/O, no model, no randomness.
    Collaborators: the caller supplies already-fetched DTOs; ``AnswerAssembler``
    consumes the result; ``GraphIdentityChecker`` re-derives the exposure from
    the graph and must agree within $0.02.
    """

    window: CardWindow | None = None
    testing: CardTestingProbe | None = None
    region: RegionNovelty | None = None
    _by_id: dict[str, TransactionRow] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for row in self.window.window if self.window else ():
            self._by_id[row.txn_id] = row

    def scope(
        self,
        flagged: TransactionRow,
        pattern: Pattern,
        verdict: Verdict,
        region_code: str = "",
    ) -> EpisodeScope:
        """Return the episode this verdict and pattern imply."""
        self._by_id.setdefault(flagged.txn_id, flagged)

        # The contract is explicit: a legitimate verdict has an empty episode and
        # zero exposure. Nothing else in this method may override that.
        if verdict is Verdict.LEGITIMATE:
            return EpisodeScope((), "", 0.0, "Verdict is legitimate, so no episode is claimed.")

        if pattern is Pattern.CARD_TESTING:
            ids, basis = self._card_testing(flagged)
        elif pattern in (
            Pattern.CARD_NOT_PRESENT_FRAUD,
            Pattern.CARD_NOT_PRESENT_NEW_DEVICE,
            Pattern.ACCOUNT_TAKEOVER,
        ):
            ids, basis = self._card_not_present(flagged)
        elif pattern is Pattern.OUT_OF_REGION_USE:
            ids, basis = self._out_of_region(flagged, region_code)
        else:
            ids, basis = (
                (flagged.txn_id,),
                "Only the flagged transaction is claimed; nothing else in the window "
                "carries a signal of its own.",
            )

        ordered = self._in_time_order(ids)
        return EpisodeScope(
            affected_txn_ids=ordered,
            first_suspicious_txn_id=ordered[0] if ordered else "",
            exposure_usd=self._exposure(ordered),
            basis=basis,
        )

    # ── per-pattern scoping ──────────────────────────────────────────────────

    def _card_testing(self, flagged: TransactionRow) -> tuple[tuple[str, ...], str]:
        """The probe authorisations plus whatever cleared after them."""
        ids = {flagged.txn_id}
        small: Sequence[str] = self.testing.small_txn_ids if self.testing else ()
        after: Sequence[str] = self.testing.purchases_after_ids if self.testing else ()
        ids.update(small)
        ids.update(after)
        return tuple(ids), (
            f"Card testing: {len(small)} small authorisation(s) in the hour before the alert "
            f"and {len(after)} purchase(s) after it."
        )

    def _card_not_present(self, flagged: TransactionRow) -> tuple[tuple[str, ...], str]:
        """Online transactions in the window that incriminate themselves."""
        ids = {flagged.txn_id}
        joined = 0
        for row in self._window_rows(flagged, timedelta(hours=CNP_WINDOW_HOURS)):
            if row.txn_id == flagged.txn_id or row.channel != "online":
                continue
            if self._incriminating(row):
                ids.add(row.txn_id)
                joined += 1
        return tuple(ids), (
            f"Card-not-present episode: the flagged transaction plus {joined} other online "
            f"transaction(s) within {CNP_WINDOW_HOURS}h that carry a signal of their own."
        )

    def _out_of_region(
        self, flagged: TransactionRow, region_code: str
    ) -> tuple[tuple[str, ...], str]:
        """Transactions in the novel region, from its first appearance onward.

        Several days of purchases in one new region is a trip, not a clone — so
        the window is the region's own history, not a fixed span.
        """
        ids = {flagged.txn_id}
        if not region_code:
            return tuple(ids), "No billing region on the alert, so only it is claimed."
        since = self.region.first_seen_in_region if self.region else None
        joined = 0
        for row in self._window_rows(flagged, timedelta(days=30)):
            if row.txn_id == flagged.txn_id or row.addr1 != region_code:
                continue
            if since is not None and row.ts is not None and row.ts < since:
                continue
            ids.add(row.txn_id)
            joined += 1
        return tuple(ids), (
            f"Out-of-region use: the flagged transaction plus {joined} other transaction(s) "
            f"in billing region {region_code}."
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _incriminating(row: TransactionRow) -> bool:
        """Does this transaction carry a signal of its own, or is it just nearby?"""
        return row.risk_score >= CNP_RISK_FLOOR or row.device_new == "New" or bool(row.proxy_flag)

    def _window_rows(self, flagged: TransactionRow, span: timedelta) -> Iterable[TransactionRow]:
        if self.window is None or flagged.ts is None:
            return ()
        return [
            row
            for row in self.window.window
            if row.ts is not None and abs(row.ts - flagged.ts) <= span
        ]

    def _in_time_order(self, ids: Iterable[str]) -> tuple[str, ...]:
        """Earliest first, so ``first_suspicious_txn_id`` is the head of the list."""
        far_future = datetime.max

        def when(txn_id: str) -> datetime:
            row = self._by_id.get(txn_id)
            return row.ts if row is not None and row.ts is not None else far_future

        return tuple(sorted(set(ids), key=lambda txn_id: (when(txn_id), txn_id)))

    def _exposure(self, ids: Sequence[str]) -> float:
        """Sum of the ABSOLUTE amounts, including the flagged transaction.

        Rounded to cents because the validator compares against the graph within
        $0.02 and float addition over a dozen amounts drifts.
        """
        total = 0.0
        for txn_id in ids:
            row = self._by_id.get(txn_id)
            if row is not None:
                total += abs(row.amt)
        return round(total, 2)
