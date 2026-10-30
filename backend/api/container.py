"""The composition root: everything built once, on the loop that will use it.

This is the API's answer to ``sentinel.cli._build_orchestrator``, and it wires
the same objects the same way — one graph repository, one model client, one
likelihood table, one policy engine. What it adds is the operational half the
CLI has no use for: the database, the event journal, the broker, and the four
services that stand between a controller and the domain.

Three decisions are worth the words.

**It is built inside the lifespan, not at import.** An ``httpx.AsyncClient``
and a SQLAlchemy async engine belong to the loop that created them; building
them at module scope and awaiting them under uvicorn is the classic way to get
"attached to a different loop" at the first request.

**The retriever is warmed in the background.** 5,611 vectors take a few
seconds to read out of the graph, and a health check that waits for them is a
health check that fails a deployment for being slow. ``/ready`` reports the
warm honestly while it runs; nothing else blocks on it.

**The one cycle in the object graph is closed here.** A denial enqueues an
approval and an approval releases an execution, so ``ApprovalService`` is built
first, handed to ``ActionExecutionService``, and given it back through
``bind`` — at wiring time, in one place, rather than by a lazy import that
would make the cycle no less real and considerably less visible.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from api.errors import ApiError
from api.handlers.actions import ActionHandlerRegistry, build_action_handlers
from api.schemas import RunMode
from api.services import (
    ActionExecutionService,
    AnswerSource,
    ApprovalService,
    AuditService,
    FileAnswerSource,
    IdempotencyService,
    RoutePermissionPolicy,
    load_alerts,
)
from api.sse import EventJournal, JournalingEventEmitter, SseBroker
from api.store import Database
from api.store.traces import TraceArchive
from sentinel.agents.orchestrator import InvestigationOrchestrator, SentinelOrchestrator
from sentinel.config.settings import Settings
from sentinel.domain.alert import Alert
from sentinel.evidence.table import EvidenceLikelihoodTable
from sentinel.graph.repository import GraphRepository
from sentinel.graph.tigergraph import TigerGraphRestRepository
from sentinel.llm.client import LlmClient
from sentinel.memory.store import CaseMemoryStore
from sentinel.policy.engine import PolicyEngine, build_policy_engine
from sentinel.policy.routing import RoutingTable
from sentinel.rag.embeddings import EmbeddingService
from sentinel.rag.retriever import GraphRagRetriever
from sentinel.validation.graph_check import GraphIdentityChecker
from sentinel.validation.validator import AnswerValidator

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunHandle:
    """One investigation in flight in this process.

    Responsibility: hold the task so a cancel can reach it, and the emitter so
    a cancel can close the stream it is feeding.
    Collaborators: ``RunRegistry`` owns the set of these; the investigation
    service fills and releases them.
    """

    run_id: str
    case_id: str
    started_at: float
    batch_id: str | None = None
    task: asyncio.Task[None] | None = None
    emitter: JournalingEventEmitter | None = None
    cancelled: bool = False


class RunRegistry:
    """Which investigations are running *now*, and the ceiling on that.

    Responsibility: admission control and lookup. It is deliberately in
    memory: a ``run`` row left saying "running" by a process that was killed
    is a historical fact, not a reason to refuse a new run, and the only thing
    that can truthfully answer "is a run in flight" is the process holding the
    task.
    Collaborators: the investigation service, which admits and releases; the
    cancel endpoint, which reaches the task through it.
    """

    def __init__(self, limit: int) -> None:
        # Settings.max_concurrent_investigations. Each run holds a graph
        # connection pool slot and a model budget; four is the measured point
        # where the workspace still answers in under a second.
        self._limit = max(1, limit)
        self._handles: dict[str, RunHandle] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def active(self) -> tuple[RunHandle, ...]:
        return tuple(self._handles.values())

    def get(self, run_id: str) -> RunHandle | None:
        return self._handles.get(run_id)

    def for_case(self, case_id: str) -> RunHandle | None:
        """The run in flight on this case, if there is one."""
        for handle in self._handles.values():
            if handle.case_id == case_id:
                return handle
        return None

    def admit(self, handle: RunHandle) -> None:
        """Take a slot, or 429 naming the ceiling that is full."""
        if len(self._handles) >= self._limit:
            raise ApiError(
                f"{len(self._handles)} investigations are already running; the limit is "
                f"{self._limit}",
                code="too_many_investigations",
                status=429,
                running=len(self._handles),
                limit=self._limit,
            )
        self._handles[handle.run_id] = handle

    def release(self, run_id: str) -> None:
        self._handles.pop(run_id, None)


@dataclass(kw_only=True)
class Container:
    """Every singleton the API serves a request from.

    Responsibility: hold the built graph, and decide — once, in :meth:`build` —
    which concrete collaborator each abstraction gets. Nothing else in the API
    constructs a dependency, which is what lets the integration suite run the
    whole app against a fake graph and a stub orchestrator with no credentials.
    Collaborators: everything. That is the definition of a composition root.
    """

    settings: Settings
    graph: GraphRepository
    db: Database
    journal: EventJournal
    #: Recorded runs from the batch runner, which this process never journalled.
    traces: TraceArchive
    broker: SseBroker
    policy: PolicyEngine
    routing: RoutingTable
    permissions: RoutePermissionPolicy
    handlers: ActionHandlerRegistry
    audit: AuditService
    idempotency: IdempotencyService
    approvals: ApprovalService
    executions: ActionExecutionService
    answers: AnswerSource
    alerts: Mapping[str, Alert]
    memory: CaseMemoryStore
    runs: RunRegistry
    #: Keyed by ``RunMode``. Only the modes this process can actually serve are
    #: present, so asking for one it cannot serve is a 422 rather than a live
    #: run wearing a ``replay`` label.
    orchestrators: Mapping[RunMode, InvestigationOrchestrator]
    validator: AnswerValidator = field(default_factory=AnswerValidator)
    llm: LlmClient | None = None
    retriever: GraphRagRetriever | None = None
    #: Absent against a fake graph: proving twenty ids exist needs the real one.
    checker: GraphIdentityChecker | None = None
    started_at: float = field(default_factory=time.monotonic)
    _stack: AsyncExitStack | None = None
    _warm: asyncio.Task[int] | None = None

    # ── the live wiring ──────────────────────────────────────────────────────

    @classmethod
    async def build(cls, settings: Settings) -> Container:
        """The live stack: real graph, real model, real write-back.

        Mirrors ``sentinel.cli._build_orchestrator`` deliberately. A terminal
        run and an HTTP run must reach the same answer, and the surest way to
        guarantee that is for the two composition roots to build the same
        objects from the same settings.
        """
        stack = AsyncExitStack()
        graph = await stack.enter_async_context(TigerGraphRestRepository.from_settings(settings))
        llm = LlmClient(settings=settings)
        # One meter across the model and the embeddings, so a case's `tokens`
        # counts every token it spent rather than only the chat ones.
        embeddings = EmbeddingService(settings=settings, meter=llm.meter)
        memory = CaseMemoryStore(graph=graph, embeddings=embeddings)
        retriever = GraphRagRetriever(settings=settings, graph=graph, embeddings=embeddings)
        orchestrator = SentinelOrchestrator(
            settings=settings,
            repository=graph,
            llm=llm,
            table=EvidenceLikelihoodTable(settings.elt_path),
            memory=memory,
            retriever=retriever,
        )

        db = Database.from_settings(settings)
        await db.create_all()
        container = cls.assemble(
            settings=settings,
            graph=graph,
            db=db,
            memory=memory,
            orchestrators={RunMode.LIVE: orchestrator},
            llm=llm,
            retriever=retriever,
            checker=GraphIdentityChecker(graph),
        )
        container._stack = stack
        return container

    @classmethod
    def assemble(
        cls,
        *,
        settings: Settings,
        graph: GraphRepository,
        db: Database,
        memory: CaseMemoryStore,
        orchestrators: Mapping[RunMode, InvestigationOrchestrator],
        llm: LlmClient | None = None,
        retriever: GraphRagRetriever | None = None,
        checker: GraphIdentityChecker | None = None,
    ) -> Container:
        """The operational half, built the same way for a live app and a test.

        Split from :meth:`build` so the integration suite assembles the API
        over a ``FakeGraphRepository`` and a stub orchestrator without
        reimplementing the service wiring — the part where the order actually
        matters.
        """
        policy = build_policy_engine()
        routing = RoutingTable(policy.config)
        permissions = RoutePermissionPolicy(routing)
        audit = AuditService(db)
        idempotency = IdempotencyService(db)
        answers: AnswerSource = FileAnswerSource(settings.cases_dir)
        # Twenty rows, read once. The handlers need the customer and card ids,
        # which the graded answer file deliberately does not carry.
        alerts = load_alerts(settings.case_pack_path)
        approvals = ApprovalService(db=db, permissions=permissions, answers=answers, audit=audit)
        handlers = build_action_handlers(memory)
        executions = ActionExecutionService(
            db=db,
            handlers=handlers,
            permissions=permissions,
            audit=audit,
            idempotency=idempotency,
            approvals=approvals,
            answers=answers,
            alerts=alerts,
        )
        # The one cycle, closed at wiring time: a denial enqueues an approval,
        # an approval releases an execution.
        approvals.bind(executions)
        return cls(
            settings=settings,
            graph=graph,
            db=db,
            journal=EventJournal(db),
            traces=TraceArchive(settings.runs_dir),
            broker=SseBroker(queue_size=settings.sse_subscriber_queue_size),
            policy=policy,
            routing=routing,
            permissions=permissions,
            handlers=handlers,
            audit=audit,
            idempotency=idempotency,
            approvals=approvals,
            executions=executions,
            answers=answers,
            alerts=alerts,
            memory=memory,
            runs=RunRegistry(settings.max_concurrent_investigations),
            orchestrators=dict(orchestrators),
            llm=llm,
            retriever=retriever,
            checker=checker,
        )

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def uptime_s(self) -> float:
        return round(time.monotonic() - self.started_at, 3)

    @property
    def warming(self) -> bool:
        """Whether the vector index is still being read out of the graph."""
        return self._warm is not None and not self._warm.done()

    def warm_in_background(self) -> None:
        """Start the retriever's warm without making anything wait for it.

        A failure here is logged and nothing else: retrieval degrades to the
        structural half, which is worse than hybrid and much better than a
        process that will not start because a workspace was asleep.
        """
        if self.retriever is None or self._warm is not None:
            return
        self._warm = asyncio.create_task(self._warm_retriever(), name="retriever-warm")

    async def _warm_retriever(self) -> int:
        retriever = self.retriever
        if retriever is None:
            return 0
        started = time.perf_counter()
        try:
            size = await retriever.warm()
        except Exception:
            logger.exception("the retriever could not be warmed; recall stays structural")
            return 0
        logger.info("retriever warm: %d vectors in %.1fs", size, time.perf_counter() - started)
        return size

    def orchestrator_for(self, mode: RunMode) -> InvestigationOrchestrator:
        """The orchestrator that can serve this mode, or a 422 naming the ones that can."""
        orchestrator = self.orchestrators.get(mode)
        if orchestrator is None:
            available = ", ".join(sorted(m.value for m in self.orchestrators))
            raise ApiError(
                f"this deployment cannot run a '{mode.value}' investigation; it serves: "
                f"{available or 'none'}",
                code="mode_unavailable",
                status=422,
                mode=mode.value,
                available=sorted(m.value for m in self.orchestrators),
            )
        return orchestrator

    def require_retriever(self) -> GraphRagRetriever:
        """The retriever, or a 503 that says which of the two reasons it is."""
        if self.retriever is None:
            raise ApiError(
                "this deployment was started without GraphRAG, so there is no retrieval to show",
                code="retrieval_unavailable",
                status=503,
            )
        if not self.retriever.ready:
            raise ApiError(
                "the vector index is still warming; retrieval is available in a few seconds",
                code="retrieval_warming",
                status=503,
            )
        return self.retriever

    async def aclose(self) -> None:
        """Close everything this container opened, in the reverse order."""
        if self._warm is not None and not self._warm.done():
            self._warm.cancel()
        for handle in self.runs.active():
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()
        await self.db.aclose()
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None


class ContainerHandle:
    """The box the lifespan fills, and every controller reads.

    Responsibility: turn "the container is not built yet" from an
    ``AttributeError`` at request time into a 503 that says so. It holds one
    reference and no logic.
    Collaborators: the app factory sets it at startup and clears it at
    shutdown; every controller resolves it per request.
    """

    def __init__(self, container: Container | None = None) -> None:
        self._container = container

    def set(self, container: Container) -> None:
        self._container = container

    def clear(self) -> None:
        self._container = None

    @property
    def ready(self) -> bool:
        return self._container is not None

    def require(self) -> Container:
        if self._container is None:
            raise ApiError(
                "the API is still starting; its graph, model and store are not built yet",
                code="starting",
                status=503,
            )
        return self._container
