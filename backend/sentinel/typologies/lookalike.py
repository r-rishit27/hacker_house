"""Fraud or look-alike? A small model fitted on the bank's own closed cases.

The hard zone of Round 1 was one shape: a single online charge from a device
new to the card, raised by the risk model. Six of seven were cleared. Neither
hand rules nor the decision model separated them — the model scored the
cleared ones 0.85–0.98.

The bank has already labelled this question 5,565 times. 833 of its 910
cleared alerts can be matched back to their flagged transaction (the notes say
"model scored a $X transaction at S"); 4,675 confirmed-fraud cases name their
first fraudulent transaction. This module computes features of a transaction
*relative to its own card's history* and scores them with weights fitted by
``eval/fit_lookalike.py`` on exactly that contrast.

``risk_score`` is deliberately not a feature. Every cleared alert was raised
by it and every confirmed case by a customer, so it measures the trigger, not
the fraud.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from sentinel.typologies.model import Txn

WEIGHTS = Path(__file__).with_name("lookalike.json")

FEATURES = (
    "log_amt_ratio", "above_p95", "product_share", "product_unseen", "email_known", "email_absent",
    "has_device", "device_seen", "device_flag_new", "proxy", "proxy_seen", "online", "region_new",
    "night", "night_share", "log_n_hist", "log_gap_h", "n_prev_24h", "new_devices_7d", "same_amt_seen",
    "round_amount", "log_days_active",
)


def features(f: Txn, hist: Sequence[Txn]) -> dict[str, float]:
    h = sorted(hist, key=lambda t: t.ts)
    amts = [t.amt for t in h] or [f.amt]
    med = statistics.median(amts)
    p95 = sorted(amts)[min(len(amts) - 1, int(0.95 * len(amts)))]
    prods = Counter(t.product for t in h)
    emails = {t.p_email for t in h if t.p_email}
    devices = {t.device_key for t in h if t.device_key}
    week = [t for t in h if f.ts - t.ts <= timedelta(days=7)]
    seen_before_week = {t.device_key for t in h if t.device_key and f.ts - t.ts > timedelta(days=7)}
    regions = {t.addr1 for t in h if t.addr1}
    gap_h = (f.ts - h[-1].ts).total_seconds() / 3600 if h else 24 * 180
    return {
        "log_amt_ratio": math.log1p(f.amt) - math.log1p(med),
        "above_p95": float(f.amt > p95),
        "product_share": prods[f.product] / len(h) if h else 0.0,
        "product_unseen": float(prods[f.product] == 0),
        "email_known": float(bool(f.p_email) and f.p_email in emails),
        "email_absent": float(not f.p_email),
        "has_device": float(bool(f.device_key)),
        "device_seen": float(bool(f.device_key) and f.device_key in devices),
        "device_flag_new": float(f.device_new == "New"),
        "proxy": float(bool(f.proxy)),
        "proxy_seen": float(any(t.proxy for t in h)),
        "online": float(f.online),
        "region_new": float(bool(f.addr1) and bool(regions) and f.addr1 not in regions),
        "night": float(0 <= f.ts.hour < 6),
        "night_share": sum(0 <= t.ts.hour < 6 for t in h) / len(h) if h else 0.0,
        "log_n_hist": math.log1p(len(h)),
        "log_gap_h": math.log1p(max(gap_h, 0.0)),
        "n_prev_24h": float(sum(f.ts - t.ts <= timedelta(hours=24) for t in h)),
        "new_devices_7d": float(len({t.device_key for t in week if t.device_key} - seen_before_week)),
        "same_amt_seen": float(any(abs(t.amt - f.amt) <= 0.01 for t in h)),
        "round_amount": float(abs(f.amt - round(f.amt)) < 0.005),
        "log_days_active": math.log1p((f.ts - h[0].ts).days) if h else 0.0,
    }


class LookalikeModel:
    """Logistic weights over standardised features, loaded from ``lookalike.json``."""

    def __init__(self, path: Path = WEIGHTS) -> None:
        spec = json.loads(Path(path).read_text())
        self.mean = spec["mean"]
        self.scale = spec["scale"]
        self.coef = spec["coef"]
        self.intercept = spec["intercept"]
        self.thresholds = spec["thresholds"]
        self.meta = spec.get("meta", {})

    def score(self, f: Txn, hist: Sequence[Txn]) -> tuple[float, list[tuple[str, float]]]:
        """P(fraud | features) at the training base rate, and each feature's contribution."""
        x = features(f, hist)
        contrib = [(k, self.coef[k] * (x[k] - self.mean[k]) / (self.scale[k] or 1.0)) for k in FEATURES]
        z = self.intercept + sum(c for _, c in contrib)
        return 1 / (1 + math.exp(-z)), sorted(contrib, key=lambda kv: -abs(kv[1]))


_MODEL: LookalikeModel | None = None


def model() -> LookalikeModel | None:
    global _MODEL
    if _MODEL is None and WEIGHTS.exists():
        _MODEL = LookalikeModel()
    return _MODEL
