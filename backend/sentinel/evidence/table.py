"""The fitted likelihood table, read once and never mutated.

``elt.json`` holds 29 features in 11 groups, fitted on 14,055 confirmed-fraud
transactions against 300,602 controls matched on card. This class is the only
reader of that file. It exposes the three lookups the ledger needs — a prior per
trigger type, a likelihood ratio per ``(feature, present)`` pair, and the group a
feature belongs to — and raises on anything it does not recognise.

Both lookups used to default silently. A feature name that is not in the table is
a typo, and a typo that posts nothing leaves the probability wrong in a way
nothing downstream can detect; a trigger type with no prior used to become 0.5
with no record that it had.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sentinel.domain.enums import TriggerType
from sentinel.domain.errors import SentinelError, UnknownFeature, UnknownTrigger

#: Where the fitted table ships. Passed explicitly by whoever builds the table —
#: nothing here reads it implicitly, and ``Settings.elt_path`` points at the same
#: file so the composition root and the tests agree without importing Settings.
DEFAULT_ELT_PATH: Path = Path(__file__).with_name("elt.json")

#: Priors the fit cannot produce, merged **over** the file's own ``trigger_priors``.
#:
#: The shipped ``elt.json`` has ``risk_score`` (0.25), ``customer_report`` (0.75)
#: and ``other`` (0.75) and no ``analyst_request`` key at all, so v1's
#: ``Ledger("analyst_request")`` fell back to 0.5 without recording that it had —
#: on HHG-014, the one analyst-request alert in the benchmark. The number is the
#: same; what changes is that it is now stated. 0.50 is uninformative by choice:
#: the closed-case history contains no analyst-request alerts to fit against, and
#: the rates it does contain are degenerate (0.0 and 1.0) and already shrunk half
#: of the way to 0.5.
#:
#: It lives in code rather than in ``elt.json`` because that file is fitted
#: output that ``python -m eval.fit_elt`` overwrites, and because a later refit
#: on a handful of analyst-request cases would produce another degenerate rate.
#: This entry therefore takes precedence over the file.
SUPPLEMENTARY_PRIORS: Mapping[str, float] = MappingProxyType(
    {TriggerType.ANALYST_REQUEST.value: 0.50}
)

#: Features the fit puts in separate groups that are one observation.
#:
#: The ledger caps correlated evidence *by group*, on the stated principle that
#: three phrasings of one finding are not three findings. The fit's own grouping
#: splits one finding four ways: ``channel_online``, ``dist1_missing``,
#: ``m_flags_all_true`` and ``device_found`` are not four facts about a
#: transaction, they are four consequences of it having happened online with an
#: identity record. Vesta populates the M flags and the device record only for
#: online transactions, and ``dist1`` is missing precisely when there is no
#: card-present distance to record.
#:
#: Measured over the twenty benchmark cases: those four groups contributed
#: +1.84 log-odds on every one of the thirteen cases that came back ``fraud``
#: and -1.45 on every one that came back ``legitimate`` — a 27x swing, applied
#: identically, for the single fact that a transaction was online. Merged, the
#: family is capped at the same +-1.2 as any other single observation.
#:
#: It lives here rather than in ``elt.json`` because that file is fitted output
#: that ``python -m eval.fit_elt`` overwrites. The fit produces the ratios; what
#: counts as one observation is a modelling decision, and this is it.
CHANNEL_GROUP = "channel"

GROUP_MERGES: Mapping[str, str] = MappingProxyType(
    {
        "channel_online": CHANNEL_GROUP,
        "dist1_missing": CHANNEL_GROUP,
        "dist1_large": CHANNEL_GROUP,
        "device_found": CHANNEL_GROUP,
        "m_flags_all_true": CHANNEL_GROUP,
        "m_flags_any_false": CHANNEL_GROUP,
        "match_status_mismatch": CHANNEL_GROUP,
    }
)

#: Keys every feature row must carry for the ledger to be able to post it.
_REQUIRED_FEATURE_KEYS: tuple[str, ...] = ("group", "lr_present", "lr_absent")


class EvidenceLikelihoodTable:
    """The fitted likelihood ratios and trigger priors, as typed lookups.

    Responsibility: own the parse of ``elt.json``, validate it at construction,
    and answer four questions about it. It holds no state that changes.

    Collaborators: :class:`~sentinel.evidence.ledger.EvidenceLedger` calls
    :meth:`prior_for` once and :meth:`lookup` / :meth:`group_of` per posting;
    ``FeatureExtractor`` calls :meth:`has` to skip features it cannot support
    yet. The path is injected — this class never consults ``Settings``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        payload = self._read(path)
        self._features: dict[str, dict[str, Any]] = self._validated_features(payload, path)
        self._priors: dict[str, float] = {
            **{
                name: float(spec["prior_used"])
                for name, spec in payload.get("trigger_priors", {}).items()
                if "prior_used" in spec
            },
            **SUPPLEMENTARY_PRIORS,
        }

    # ── construction ─────────────────────────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise SentinelError(
                f"the fitted likelihood table is missing at {path}. "
                "Fit it first: python -m eval.fit_elt",
                path=str(path),
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SentinelError(
                f"the fitted likelihood table at {path} is not valid JSON: {exc}",
                path=str(path),
            ) from exc
        if not isinstance(payload, dict):
            raise SentinelError(
                f"the fitted likelihood table at {path} is not a JSON object",
                path=str(path),
            )
        return payload

    @staticmethod
    def _validated_features(payload: Mapping[str, Any], path: Path) -> dict[str, dict[str, Any]]:
        """Reject a half-fitted row at load time rather than mid-investigation."""
        features = payload.get("features")
        if not isinstance(features, dict) or not features:
            raise SentinelError(f"no features in the fitted table at {path}", path=str(path))
        for name, spec in features.items():
            missing = [key for key in _REQUIRED_FEATURE_KEYS if key not in spec]
            if missing:
                raise SentinelError(
                    f"feature {name!r} in {path.name} is missing {', '.join(missing)}",
                    feature=name,
                    missing=missing,
                )
        return dict(features)

    # ── lookups ──────────────────────────────────────────────────────────────

    def prior_for(self, trigger_type: TriggerType | str) -> float:
        """The prior fraud probability implied by how the alert arrived.

        Raises :class:`UnknownTrigger` rather than returning 0.5, so an
        unpriced trigger type is a loud failure and not a quiet assumption.
        ``analyst_request`` is priced by :data:`SUPPLEMENTARY_PRIORS`.
        """
        key = _key(trigger_type)
        try:
            return self._priors[key]
        except KeyError:
            raise UnknownTrigger(
                f"no prior is defined for trigger type {key!r}",
                trigger_type=key,
                known=sorted(self._priors),
            ) from None

    def lookup(self, feature: str, present: bool) -> float:
        """The likelihood ratio for a feature being present, or being absent.

        A detector that ran and found nothing posts ``lr_absent``, which is how
        the ledger can argue a case is legitimate instead of merely failing to
        find anything.
        """
        spec = self._spec(feature)
        return float(spec["lr_present" if present else "lr_absent"])

    def group_of(self, feature: str) -> str:
        """The correlated-evidence group the feature belongs to.

        Not always the group the fit assigned: see :data:`GROUP_MERGES`.
        """
        fitted = str(self._spec(feature)["group"])
        return GROUP_MERGES.get(feature, fitted)

    def fitted_group_of(self, feature: str) -> str:
        """The group as the fit wrote it, before any merge. For the audit trail."""
        return str(self._spec(feature)["group"])

    def has(self, feature: str) -> bool:
        """Whether the feature was fitted. Does not raise."""
        return feature in self._features

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Every fitted feature name, in file order."""
        return tuple(self._features)

    @property
    def trigger_types(self) -> tuple[str, ...]:
        """Every trigger type that has a prior, including the supplements."""
        return tuple(self._priors)

    @property
    def path(self) -> Path:
        """The file this table was read from."""
        return self._path

    def _spec(self, feature: str) -> Mapping[str, Any]:
        try:
            return self._features[feature]
        except KeyError:
            raise UnknownFeature(
                f"{feature!r} is not in the fitted table",
                feature=feature,
            ) from None


def _key(trigger_type: TriggerType | str) -> str:
    # TriggerType mixes in str, but str() on a 3.11 mixin enum returns
    # "TriggerType.RISK_SCORE", not "risk_score".
    return trigger_type.value if isinstance(trigger_type, Enum) else str(trigger_type)
