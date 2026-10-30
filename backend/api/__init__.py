"""The HTTP surface: FastAPI over the same orchestrator the CLI runs.

The API adds no fraud logic. Every decision it serves was made in
``sentinel``; what lives here is transport, persistence of operational state,
the permission boundary and the event stream. The one thing it does *decide* is
who may execute what — and even that is a recomputation of the policy engine's
own routing table, never a second opinion about it.
"""

__version__ = "2.0.0"
