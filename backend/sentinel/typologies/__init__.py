"""Detectors for fraud the policy's five patterns do not name, and for the
legitimate activity that looks like fraud.

Round 1 lost on two fronts, and this package answers both:

* **Undocumented typologies.** Both hidden patterns of Round 1 were cross-card:
  four online charges just under $500 inside forty minutes, repeated on a dozen
  cards in the same fortnight; and one device profile, new to every account it
  touched, carrying fraud on twenty-seven cards. A per-card ledger cannot see
  either. Each detector here asks a question about *other* cards.
* **Look-alikes.** Every Round 1 case we wrongly called fraud was a single
  anomaly the card's own history explained — a new device at the usual amount,
  a large purchase in the usual product, a late-night charge on a card that
  shops late. :mod:`sentinel.typologies.explain` asks that question explicitly.

Detectors are pure functions over :class:`Txn` rows fetched through a
:class:`TxnSource`, so the same code runs against the local DuckDB lab for
backtests and against TigerGraph in a live investigation.
"""

from sentinel.typologies.model import Explanation, Txn, TxnSource, TypologyHit
from sentinel.typologies.detectors import DETECTORS, run_detectors
from sentinel.typologies.explain import EXPLAINERS, explain

__all__ = [
    "DETECTORS",
    "EXPLAINERS",
    "Explanation",
    "Txn",
    "TxnSource",
    "TypologyHit",
    "explain",
    "run_detectors",
]
