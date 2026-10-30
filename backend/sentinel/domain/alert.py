"""The alert that starts an investigation, and the case pack it is read from.

Two measured facts about the twenty benchmark alerts are encoded here, because
both are traps that cost real score if a caller trusts the row at face value.

**The alert's ``risk_score`` is not the model's score for the transaction.** It
is blank in ``case_pack.csv`` and ``-1.0`` in ``data/staging/alerts.csv`` on the
nine alerts that arrived by customer report or analyst request. ``-1`` is the
ETL's missing-numeric sentinel, so :class:`Alert` parses it to ``None``. Feeding
it into the evidence ledger's risk bands puts those nine cases in the "score
below 0.50" band by accident — and they belong there anyway, since their real
scores run 0.05 to 0.48, which is exactly why the bug survives unnoticed until
the one case whose true score matters. The model score lives on
``Transaction.risk_score`` and is fetched with ``txn_detail``.

**``opened_at`` trails the flagged transaction by one to six hours, never zero.**
Measured across all twenty alerts against ``data/staging/transactions.csv.gz``.
Anchoring a ``card_window`` or a novelty cutoff on ``opened_at`` therefore drags
up to six hours of post-alert activity into evidence that is supposed to describe
what was knowable at the time. The anchor is the flagged transaction's ``ts``;
:attr:`Alert.window_anchor_txn_id` exists to say so at the call site.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sentinel.domain.enums import TriggerType

#: The ETL's missing-numeric sentinel. Anything at or below it is "not observed",
#: which also catches TigerGraph's ``-1.797693134862316e+308`` double sentinel.
MISSING_RISK_SCORE = -1.0

#: Measured lag of ``opened_at`` behind the flagged transaction, in hours, over
#: all twenty alerts. The minimum is 1: the lag is never zero, so ``opened_at``
#: is never a safe stand-in for the transaction timestamp.
ALERT_LAG_MIN_HOURS = 1
ALERT_LAG_MAX_HOURS = 6

#: Status of a freshly read case-pack row. ``case_pack.csv`` carries no status
#: column; the staged ``alerts.csv`` writes this same value.
DEFAULT_ALERT_STATUS = "new"


class Alert(BaseModel):
    """One row of the case pack: the work item an investigation starts from.

    Responsibility: hold the nine alert fields with the two dataset traps already
    neutralised — the missing-score sentinel parsed to ``None``, and
    ``opened_at`` kept distinct from the transaction timestamp.
    Collaborators: :class:`CasePackLoader` builds it; ``InvestigationContext``
    carries it through every step; :class:`TriggerContext` derives from it.
    """

    model_config = ConfigDict(extra="forbid")

    alert_id: str
    #: When the alert was raised — *not* when the transaction happened. See the
    #: module docstring: this lags the transaction by one to six hours.
    opened_at: datetime
    trigger_type: TriggerType
    trigger_text: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    #: The score quoted in the alert, or ``None`` when the alert carried none.
    #: Never a risk band input; use ``Transaction.risk_score`` for that.
    risk_score: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str = DEFAULT_ALERT_STATUS

    @field_validator("risk_score", mode="before")
    @classmethod
    def _null_missing_score(cls, value: object) -> object:
        """Map the blank cell and the ``-1`` sentinel to ``None`` before range checks."""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            value = float(text)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) <= MISSING_RISK_SCORE
        ):
            return None
        return value

    @property
    def has_model_score(self) -> bool:
        """Whether the alert quoted a score at all. False on nine of the twenty."""
        return self.risk_score is not None

    @property
    def window_anchor_txn_id(self) -> str:
        """The id whose ``ts`` anchors every time window for this case.

        Read this instead of reaching for ``opened_at``: the alert is raised one
        to six hours after the fact, so windows centred on it are shifted.
        """
        return self.flagged_txn_id

    def lag_from(self, txn_ts: datetime) -> timedelta:
        """How far this alert trails the flagged transaction."""
        return self.opened_at - txn_ts


@dataclass(frozen=True, slots=True)
class TriggerContext:
    """What the trigger alone tells the investigation, before any graph call.

    Responsibility: name the three things that differ by trigger type, so no
    caller has to re-derive them from ``trigger_text`` and none of them is
    confused with a graph fact.
    Collaborators: ``EvidenceLedger`` takes ``trigger_type`` for its prior;
    ``EvidenceSimulator`` reads ``customer_already_denied``; ``PlannerAgent``
    reads ``analyst_hint``.
    """

    trigger_type: TriggerType
    flagged_txn_id: str
    card_id: str
    customer_id: str
    #: A ``customer_report`` alert *is* a denial, already in hand at step zero.
    #: Eight of the twenty start this way, and on those the customer's word is
    #: evidence the investigation owns before it asks for anything.
    customer_already_denied: bool
    #: The score quoted in the alert text, or ``None``. Named to make it hard to
    #: mistake for the transaction's own model score.
    model_score_at_alert: float | None
    #: The analyst's stated lead, on the one ``analyst_request`` alert. Empty
    #: otherwise. HHG-014's lead — cards sharing an unusual device profile — is
    #: the only statement of what that investigation is actually for.
    analyst_hint: str

    @classmethod
    def from_alert(cls, alert: Alert) -> TriggerContext:
        """Project an alert onto the facts its trigger contributes."""
        return cls(
            trigger_type=alert.trigger_type,
            flagged_txn_id=alert.flagged_txn_id,
            card_id=alert.card_id,
            customer_id=alert.customer_id,
            customer_already_denied=alert.trigger_type is TriggerType.CUSTOMER_REPORT,
            model_score_at_alert=alert.risk_score,
            analyst_hint=(
                alert.trigger_text if alert.trigger_type is TriggerType.ANALYST_REQUEST else ""
            ),
        )


class CasePackLoader:
    """Read the benchmark alerts from the organiser's case pack.

    Responsibility: turn a case-pack CSV into :class:`Alert` objects. It owns the
    column mapping and nothing else — the sentinel handling lives on the model,
    so an alert built from the graph gets the same treatment as one built here.
    Collaborators: ``Settings.case_pack_path`` supplies the path; the
    orchestrator and the API's queue endpoint consume the alerts.
    """

    #: ``case_pack.csv`` names the id column ``case_id``; ``data/staging/alerts.csv``,
    #: written by the ETL from the same rows, names it ``alert_id``. One loader reads both.
    _ID_COLUMNS = ("alert_id", "case_id")

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """The CSV this loader reads."""
        return self._path

    def load(self) -> list[Alert]:
        """Every alert in the pack, in file order."""
        with self._path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        return [self._to_alert(row) for row in rows]

    def load_by_id(self) -> dict[str, Alert]:
        """The same alerts keyed by ``alert_id``."""
        return {alert.alert_id: alert for alert in self.load()}

    def _to_alert(self, row: Mapping[str, str]) -> Alert:
        """Select the nine contract columns; the staged file carries extras."""
        return Alert.model_validate(
            {
                "alert_id": self._alert_id(row),
                "opened_at": row["opened_at"],
                "trigger_type": row["trigger_type"],
                "trigger_text": row["trigger_text"],
                "flagged_txn_id": row["flagged_txn_id"],
                "card_id": row["card_id"],
                "customer_id": row["customer_id"],
                "risk_score": row.get("risk_score"),
                "status": row.get("status") or DEFAULT_ALERT_STATUS,
            }
        )

    def _alert_id(self, row: Mapping[str, str]) -> str:
        for column in self._ID_COLUMNS:
            value = row.get(column)
            if value:
                return value
        raise KeyError(f"{self._path}: no alert id column; expected one of {self._ID_COLUMNS}")
