"""Offline data preparation and one-shot graph loading.

Separate from ``backend/sentinel`` on purpose, and renamed out of the way of it.
Until this package was called ``etl`` it was called ``sentinel``, and it sat at
the repository root — so ``import sentinel`` from the root resolved to *this*
code rather than to the installed ``backend/sentinel``, and `python -m sentinel`
ran the wrong package. Naming it for what it is removes the collision and the
question.

What lives here is the part of v1 that was never superseded: the pyTigerGraph
connection the bulk loaders need, the dataset's column dictionary, and the
staging paths. Everything else v1 had — the policy engine, the ledger, the
tools, the case write-back — now lives in ``backend/sentinel`` as classes, with
its tests, and the v1 copies were deleted rather than left to drift.
"""
