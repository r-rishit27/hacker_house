"""Liveness, readiness and the contract itself.

The three endpoints that answer questions about the process rather than about
a case. They are separate on purpose:

``/health`` says the process is up and touches nothing. A load balancer polls
it every few seconds and it must not be able to fail because a graph workspace
went to sleep.

``/ready`` probes the dependencies and returns 503 when one of them is not
there. The Savanna workspace auto-stops and takes about 45 seconds to wake, so
this is the endpoint that tells the difference between "the API is broken" and
"the graph is waking" — with a five-second cap, because a readiness probe that
hangs for the whole 45 is worse than one that says no and is asked again.

``/meta`` is the runtime half of ADR-7: the enums, the routing table and the
thresholds served from the objects that own them, so the console never
hand-types ``"BLOCK_ALL_CARDS"`` and can be checked against the running server.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from sqlalchemy import text

from api.container import ContainerHandle
from api.controller import ApiController, route
from api.errors import ApiError
from api.schemas import Health, Meta, Ready, ReadyCheck, ServiceStatus
from sentinel.domain.errors import SentinelError

#: A readiness probe answers, or says no. The workspace's ~45 s wake is a real
#: state, not something to hold a connection open through.
PROBE_TIMEOUT_S = 5.0

#: The case pack the whole benchmark is defined over.
EXPECTED_ALERTS = 20


class SystemController(ApiController):
    """Process-level endpoints, and the contract the console is built against.

    Responsibility: answer without depending on anything that can be asleep
    (``/health``), and probe everything that can (``/ready``).
    Collaborators: the container, for the objects being probed; ``Meta``,
    which reads the domain rather than restating it.
    """

    prefix = ""
    tags: ClassVar[list[str]] = ["system"]

    def __init__(self, handle: ContainerHandle) -> None:
        super().__init__(handle)
        # Process uptime, not container uptime: /health must answer during the
        # startup window, before there is a container to ask.
        self._started = time.monotonic()

    def routes(self) -> list[dict[str, Any]]:
        return [
            route("/health", self.health, summary="Liveness. Touches no dependency."),
            route(
                "/ready",
                self.ready,
                summary="Readiness: the store, the case pack, the graph and the vector index.",
                errors={503: "One or more dependencies are not ready."},
            ),
            route("/meta", self.meta, summary="Enums, routing, thresholds and the tool names."),
        ]

    async def health(self) -> Health:
        """The process is serving. Degraded until the container is built."""
        return Health(
            status=ServiceStatus.OK if self._handle.ready else ServiceStatus.DEGRADED,
            uptime_s=round(time.monotonic() - self._started, 3),
        )

    async def ready(self) -> Ready:
        """Probe every dependency, and 503 if any of them is not there."""
        checks = [
            await _probe("store", self._store),
            await _probe("case_pack", self._case_pack),
            await _probe("answers", self._answers),
            await _probe("graph", self._graph),
            await _probe("retrieval", self._retrieval),
        ]
        if not all(check.ok for check in checks):
            failed = ", ".join(check.name for check in checks if not check.ok)
            raise ApiError(
                f"not ready: {failed}",
                code="not_ready",
                status=503,
                checks=[check.model_dump(mode="json") for check in checks],
            )
        return Ready(ok=True, checks=checks)

    async def meta(self) -> Meta:
        """The whole contract, from the objects that own it."""
        return Meta.current(self.container.policy.config)

    # ── the probes ───────────────────────────────────────────────────────────

    async def _store(self) -> str:
        async with self.container.db.session() as session:
            await session.execute(text("select 1"))
        return self.container.db.url.split("///")[-1]

    async def _case_pack(self) -> str:
        count = len(self.container.alerts)
        if count != EXPECTED_ALERTS:
            raise ApiError(
                f"the case pack holds {count} alerts, not {EXPECTED_ALERTS}",
                code="case_pack_incomplete",
                status=503,
            )
        return f"{count} alerts"

    async def _answers(self) -> str:
        directory = self.container.settings.cases_dir
        written = len(list(directory.glob("*.json"))) if directory.is_dir() else 0
        # Not a failure: nineteen of the twenty are legitimately missing for
        # most of a benchmark run, and the queue renders either way (FR-27).
        return f"{written} of {EXPECTED_ALERTS} answer files in {directory.name}/"

    async def _graph(self) -> str:
        counts = await self.container.graph.stat_vertex_counts()
        transactions = counts.get("Transaction", 0)
        if transactions <= 0:
            raise ApiError(
                "the graph answered but holds no transactions",
                code="graph_empty",
                status=503,
            )
        return f"{len(counts)} vertex types, {transactions:,} transactions"

    async def _retrieval(self) -> str:
        if self.container.retriever is None:
            return "not configured; recall stays structural"
        if self.container.warming:
            raise ApiError(
                "the vector index is still being read out of the graph",
                code="retrieval_warming",
                status=503,
            )
        self.container.require_retriever()
        return "vector index warm"


async def _probe(name: str, check: Callable[[], Awaitable[str]]) -> ReadyCheck:
    """Run one probe under a timeout and turn any failure into a check row.

    Every failure mode is reported rather than raised: the point of ``/ready``
    is to list which dependency is missing, and an exception escaping here
    would report the first one and hide the rest.
    """
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(check(), timeout=PROBE_TIMEOUT_S)
        ok = True
    except TimeoutError:
        detail, ok = f"no answer in {PROBE_TIMEOUT_S:.0f}s", False
    except SentinelError as exc:
        detail, ok = f"{exc.code}: {exc.message}", False
    except OSError as exc:
        detail, ok = f"{type(exc).__name__}: {exc}", False
    return ReadyCheck(
        name=name,
        ok=ok,
        detail=str(detail),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
