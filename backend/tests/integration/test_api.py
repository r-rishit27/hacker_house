"""The API, end to end, with no network and no credentials.

Everything here runs against `FakeGraphRepository`, an in-memory SQLite and a
stub orchestrator, so the suite is the same in CI as on a laptop with the
Savanna workspace asleep. What is *not* stubbed is the part that matters: the
real `Container.assemble`, the real `RoutePermissionPolicy`, the real
`ActionExecutionService` and the real journal. The wiring is the thing under
test — every defect the API had was a seam between two components that were
each individually correct.

The permission walk in :func:`test_the_permission_boundary_end_to_end` is
M7's exit test, minus the browser.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from api.container import Container
from api.main import create_app
from api.schemas import RunMode
from api.store.session import Database
from sentinel.agents.emitter import EventEmitter
from sentinel.agents.orchestrator import InvestigationOrchestrator, InvestigationResult
from sentinel.config.settings import Settings
from sentinel.domain.alert import Alert
from sentinel.domain.answer import AnswerFile
from sentinel.graph.fake import FakeGraphRepository
from sentinel.memory.store import CaseMemoryStore
from sentinel.validation.validator import ValidationReport

ROOT = Path(__file__).resolve().parents[3]

#: The case the whole suite acts on. It is in the pack, it has an answer on
#: disk, and its final actions include an `L1` — which is what makes the
#: permission walk possible without inventing a fixture.
CASE = "HHG-004"


# ── the stubs ────────────────────────────────────────────────────────────────


class StubOrchestrator(InvestigationOrchestrator):
    """Emits the run-level events and nothing else.

    Enough for the journal, the SSE stream and the run row to be exercised;
    the agent itself has its own integration test against the fake graph.
    """

    def __init__(self, answer: AnswerFile) -> None:
        self._answer = answer

    async def run(self, alert: Alert, emitter: EventEmitter | None = None) -> InvestigationResult:
        assert emitter is not None
        emitter.emit(
            "run.started",
            {"case_id": alert.alert_id, "trigger_type": alert.trigger_type.value, "prior": 0.25},
        )
        for step in range(1, 4):
            emitter.emit("step.started", {"step": step, "name": "scope"}, step=step)
            emitter.emit("step.completed", {"step": step, "name": "scope"}, step=step)
        emitter.emit("run.completed", {"status": "completed", "tool_calls": 0, "tokens": 0})
        return InvestigationResult(
            answer=self._answer,
            validation=ValidationReport(),
            context=None,  # type: ignore[arg-type]
        )


def settings_for(tmp_path: Path) -> Settings:
    """Real paths for the case pack and the answers, a scratch database."""
    return Settings(
        tg_host="https://example.invalid",
        tg_secret="secret",
        openai_api_key="key",
        openai_model="stub",
        openai_embedding_model="stub",
        root=ROOT,
        cases_dir=ROOT / "cases",
        case_pack_path=ROOT / "case_pack.csv",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    settings = settings_for(tmp_path)
    answer = AnswerFile.model_validate_json((settings.cases_dir / f"{CASE}.json").read_text())
    graph = FakeGraphRepository()
    database = Database(settings.db_url)
    container = Container.assemble(
        settings=settings,
        graph=graph,
        db=database,
        # dry_run: a fake graph accepts a write, but claiming a vertex exists
        # when nothing checked would be exactly the dishonesty the real store
        # is written to avoid.
        memory=CaseMemoryStore(graph=graph, dry_run=True),
        orchestrators={RunMode.LIVE: StubOrchestrator(answer)},
    )
    app = create_app(settings, container=container)
    transport = httpx.ASGITransport(app=app)
    # ASGITransport does not run the lifespan, and the container is built
    # there on purpose — so entering it explicitly is what makes this the
    # same application the server runs rather than a hollowed-out one.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://api") as http,
    ):
        yield http
    await database.aclose()


def as_(role: str) -> dict[str, str]:
    return {"X-Sentinel-Role": role}


# ── the process ──────────────────────────────────────────────────────────────


async def test_health_answers_without_touching_a_dependency(client: httpx.AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_meta_serves_the_contract_from_the_objects_that_own_it(client: httpx.AsyncClient):
    """ADR-7: the console reads the vocabulary rather than hand-typing it."""
    body = (await client.get("/api/meta")).json()
    assert len(body["enums"]["actions"]) == 14
    assert body["enums"]["routes"] == ["auto", "L1", "L2"]
    # The one exposure-dependent route in the whole policy, served from the
    # config the engine actually decides with.
    assert body["routing"]["block_card_l2_threshold"] == 2500.0
    assert body["thresholds"]["stop_high"] == 0.85
    assert body["thresholds"]["stop_low"] == 0.15
    assert body["routing"]["approvers"]["L2"] == ["fraud_manager"]


# ── the queue ────────────────────────────────────────────────────────────────


async def test_the_queue_is_the_case_pack_not_the_answer_directory(client: httpx.AsyncClient):
    """All twenty are listed, answered or not. FR-27.

    For most of a benchmark run most cases have no answer, and a queue that
    listed only finished ones would be empty for the six minutes worth
    watching.
    """
    body = (await client.get("/api/cases", params={"limit": 100})).json()
    assert body["total"] == 20
    assert all(row["case_id"].startswith("HHG-") for row in body["items"])


async def test_a_case_that_is_not_in_the_pack_is_a_404(client: httpx.AsyncClient):
    response = await client.get("/api/cases/HHG-999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "not_found"


async def test_the_answer_is_served_verbatim(client: httpx.AsyncClient):
    served = (await client.get(f"/api/cases/{CASE}/answer")).json()
    on_disk = json.loads((ROOT / "cases" / f"{CASE}.json").read_text())
    assert served == on_disk


async def test_the_sar_endpoint_answers_even_when_nothing_is_filed(client: httpx.AsyncClient):
    """ "We considered a report and here is why not" is the examiner's answer."""
    response = await client.get(f"/api/cases/{CASE}/sar")
    assert response.status_code == 200
    body = response.json()
    assert body["sar"]["reason"], "a decision not to file still needs its reason"
    assert body["rendered"]


# ── the permission boundary: M7 ──────────────────────────────────────────────


async def test_the_action_plan_recomputes_every_route(client: httpx.AsyncClient):
    """ADR-6: the route comes from the routing table, never from the file."""
    plan = (await client.get(f"/api/cases/{CASE}/actions", headers=as_("analyst"))).json()
    assert plan["role"] == "analyst"
    blocks = [row for row in plan["final"] if row["action"] == "BLOCK_CARD"]
    assert blocks, f"{CASE} is the fixture because it recommends a block"
    assert blocks[0]["route"] in {"L1", "L2"}
    assert blocks[0]["may_execute"] is False


async def test_the_permission_boundary_end_to_end(client: httpx.AsyncClient):
    """M7, minus the browser: refused, enqueued, refused again, then approved.

    Every step is the server's answer, not the client's belief about it.
    """
    # 1. An analyst may not execute a block, and the refusal carries the
    #    approval it enqueued so the console needs no second request.
    denied = await client.post(
        "/api/actions/execute",
        headers=as_("analyst"),
        json={"case_id": CASE, "action": "BLOCK_CARD", "phase": "final"},
    )
    assert denied.status_code == 403
    problem = denied.json()
    assert problem["code"] == "forbidden_route"
    assert problem["title"] == "Action requires approval"
    assert "fraud_manager" in problem["roles_that_can_approve"]
    approval_id = problem["approval"]["approval_id"]

    # 2. The denial is a row. An audit log of successes could not answer
    #    "did anyone try to block this card?".
    audit = (await client.get("/api/audit")).json()
    assert any(
        row["outcome"] == "denied" and row["action"] == "BLOCK_CARD" for row in audit["items"]
    )

    # 3. Pressing it again enqueues nothing new: the partial unique index on a
    #    pending approval makes the denial idempotent.
    again = await client.post(
        "/api/actions/execute",
        headers=as_("analyst"),
        json={"case_id": CASE, "action": "BLOCK_CARD", "phase": "final"},
    )
    assert again.status_code == 403
    assert again.json()["approval"]["approval_id"] == approval_id

    # 4. The analyst cannot approve their own request. The route is
    #    re-asserted at decision time, not trusted from the row.
    self_approved = await client.post(
        f"/api/approvals/{approval_id}/approve", headers=as_("analyst")
    )
    assert self_approved.status_code == 403
    assert self_approved.json()["code"] == "role_insufficient_for_approval"

    # 5. A fraud manager can, and approving executes.
    approved = await client.post(
        f"/api/approvals/{approval_id}/approve", headers=as_("fraud_manager")
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["approval"]["status"] == "approved"
    assert body["execution"]["outcome"] == "executed"
    assert body["execution"]["simulated"] is True, "only CREATE_CASE is not simulated"
    assert body["execution"]["approval_id"] == approval_id


async def test_an_auto_action_executes_without_an_approval(client: httpx.AsyncClient):
    response = await client.post(
        "/api/actions/execute",
        headers=as_("analyst"),
        json={"case_id": CASE, "action": "MONITOR_CARD", "phase": "final"},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "executed"
    assert response.json()["route"] == "auto"


async def test_the_same_idempotency_key_does_not_act_twice(client: httpx.AsyncClient):
    headers = as_("analyst") | {"Idempotency-Key": "key-monitor-1"}
    body = {"case_id": CASE, "action": "MONITOR_CARD", "phase": "final"}
    first = await client.post("/api/actions/execute", headers=headers, json=body)
    second = await client.post("/api/actions/execute", headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]

    audit = (await client.get("/api/audit", params={"limit": 200})).json()
    replayed = [row for row in audit["items"] if row["outcome"] == "replayed"]
    assert replayed, "a replay is a row too, or the log cannot explain the second press"


# ── the stream ───────────────────────────────────────────────────────────────


async def test_a_run_journals_its_events_and_replays_with_no_gap(client: httpx.AsyncClient):
    """The journal row is written before the event is published.

    That ordering is what makes `Last-Event-ID` resumption a guarantee rather
    than a race, so the test asserts the guarantee: resuming at n returns
    n+1 onward, contiguous, with nothing repeated.
    """
    started = await client.post(
        "/api/investigations", json={"case_id": CASE, "mode": "live", "force": True}
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    # The run is a background task; poll its own endpoint rather than sleeping.
    for _ in range(200):
        detail = (await client.get(f"/api/investigations/{run_id}")).json()
        if detail["status"] in {"completed", "failed"}:
            break
    assert detail["status"] == "completed", detail.get("error")

    whole = (await client.get(f"/api/investigations/{run_id}/events.json")).json()["events"]
    assert [event["seq"] for event in whole] == list(range(1, len(whole) + 1))
    assert whole[0]["type"] == "run.started"
    assert whole[-1]["type"] == "run.completed"

    resumed = (
        await client.get(f"/api/investigations/{run_id}/events.json", params={"from": 2})
    ).json()["events"]
    assert [event["seq"] for event in resumed] == [e["seq"] for e in whole if e["seq"] > 2]


async def test_the_sse_stream_carries_a_retry_hint_and_an_id_per_event(
    client: httpx.AsyncClient,
):
    started = await client.post(
        "/api/investigations", json={"case_id": CASE, "mode": "live", "force": True}
    )
    run_id = started.json()["run_id"]
    for _ in range(200):
        if (await client.get(f"/api/investigations/{run_id}")).json()["status"] == "completed":
            break

    async with client.stream(
        "GET", f"/api/investigations/{run_id}/events", headers={"Last-Event-ID": "0"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert body.startswith("retry: 2000"), "a browser needs the reconnect interval first"
    ids = [int(line.split(":", 1)[1]) for line in body.splitlines() if line.startswith("id:")]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert ": keepalive" not in body.split("\n\n")[1], "a keepalive must not take an id"


# ── the benchmark ────────────────────────────────────────────────────────────


async def test_the_report_scores_the_pack_and_says_what_is_wrong_with_it(
    client: httpx.AsyncClient,
):
    body = (await client.get("/api/benchmark/report")).json()
    assert body["total"] == 20
    assert 0.0 <= body["block_rate"] <= 1.0
    assert sum(bin_["count"] for bin_ in body["probability_histogram"]) == body["valid"]
    assert isinstance(body["warnings"], list)


async def test_an_unparseable_body_is_a_problem_not_a_stack_trace(client: httpx.AsyncClient):
    response = await client.post("/api/actions/execute", json={"case_id": CASE})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "unprocessable"
