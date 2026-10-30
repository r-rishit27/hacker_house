"""The model boundary, tested without calling a model.

The live smoke test lives in ``tests/live/test_llm.py``; everything here runs on
a stub so the suite needs no credential and no network. What matters most is the
schema conversion: OpenAI's strict mode is narrower than JSON Schema, and a
schema it rejects fails the whole run at the first agent call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel, Field

from sentinel.config.settings import Settings
from sentinel.domain.enums import Pattern
from sentinel.domain.errors import LlmUnavailable, StructuredOutputError
from sentinel.llm.client import CostMeter, LlmClient, json_schema_for, price_of


class Claim(BaseModel):
    posting_id: str
    claim: str


class Assessment(BaseModel):
    """The shape the assessment agent demands back."""

    pattern: Pattern
    pattern_description: str = ""
    claims: list[Claim] = Field(default_factory=list)
    summary: str = ""


def settings(**overrides: Any) -> Settings:
    base = dict(
        tg_host="https://example.invalid",
        tg_secret="secret",
        openai_api_key="test-key",
        openai_model="gpt-5.4-mini",
        openai_embedding_model="text-embedding-3-large",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ── strict schema conversion ─────────────────────────────────────────────────


def test_every_property_is_required_in_strict_mode():
    # Strict mode demands it, even for fields Pydantic gave a default.
    schema = json_schema_for(Assessment)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_nested_models_are_inlined_because_refs_are_rejected():
    schema = json_schema_for(Assessment)
    assert "$defs" not in schema
    claims = schema["properties"]["claims"]
    assert "$ref" not in str(claims)
    assert claims["items"]["properties"].keys() == {"posting_id", "claim"}
    assert claims["items"]["additionalProperties"] is False


def test_an_enum_field_keeps_its_seven_permitted_values():
    # The pattern vocabulary is what stops the model inventing a category.
    schema = json_schema_for(Assessment)
    assert set(schema["properties"]["pattern"]["enum"]) == {p.value for p in Pattern}


def test_defaults_and_titles_are_stripped():
    schema = json_schema_for(Assessment)
    rendered = str(schema)
    assert "'default'" not in rendered
    assert "'title'" not in rendered


# ── cost accounting ──────────────────────────────────────────────────────────


def test_the_meter_reports_what_the_answer_file_needs():
    meter = CostMeter()
    meter.charge("gpt-5.4-mini", 4_000, 800)
    meter.charge("gpt-5.4-mini", 2_000, 300)
    assert meter.prompt_tokens == 6_000
    assert meter.completion_tokens == 1_100
    assert meter.tokens == 7_100, "answer.tokens is the sum, and must never be 0 on a real run"
    assert meter.calls == 2
    assert meter.usd > 0


def test_a_dated_snapshot_prices_as_its_base_model():
    assert price_of("gpt-5.4-mini-2026-03-17", 1_000_000, 0) == price_of(
        "gpt-5.4-mini", 1_000_000, 0
    )


def test_an_unknown_model_costs_zero_rather_than_a_guess():
    # The budget guard trusts this number; a wrong price is worse than none.
    assert price_of("some-model-we-have-never-seen", 1_000_000, 1_000_000) == 0.0


# ── the call path, against a stub ────────────────────────────────────────────


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


class _StubCompletions:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []
        self.finish_reasons: list[str] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply

        class _Message:
            content = reply

        reason = (
            self.finish_reasons[len(self.calls) - 1]
            if len(self.calls) <= len(self.finish_reasons)
            else "stop"
        )

        class _Choice:
            message = _Message()
            finish_reason = reason

        class _Completion:
            def __init__(self) -> None:
                self.choices = [_Choice()]
                self.usage = _Usage(120, 40)

        return _Completion()


class _StubClient:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.completions = _StubCompletions(replies)
        self.chat = self
        self.embeddings = self


def client_with(replies: list[str | Exception]) -> tuple[LlmClient, _StubClient]:
    stub = _StubClient(replies)
    return LlmClient(settings=settings(), client=stub), stub  # type: ignore[arg-type]


async def test_a_valid_reply_is_returned_typed_and_charged():
    client, _ = client_with(
        ['{"pattern":"card_testing","pattern_description":"","claims":[],"summary":"s"}']
    )
    out = await client.complete(purpose="claims", system="sys", user="usr", schema=Assessment)
    assert out.value.pattern is Pattern.CARD_TESTING
    assert out.total_tokens == 160
    assert client.meter.tokens == 160


async def test_max_tokens_is_never_sent():
    # This model family rejects max_tokens outright — measured, not assumed.
    client, stub = client_with(
        ['{"pattern":"none","pattern_description":"","claims":[],"summary":""}']
    )
    await client.complete(purpose="claims", system="s", user="u", schema=Assessment)
    sent = stub.completions.calls[0]
    assert "max_tokens" not in sent
    assert sent["max_completion_tokens"] > 0


async def test_the_call_is_deterministic_by_construction():
    client, stub = client_with(
        ['{"pattern":"none","pattern_description":"","claims":[],"summary":""}']
    )
    await client.complete(purpose="claims", system="s", user="u", schema=Assessment)
    sent = stub.completions.calls[0]
    assert sent["temperature"] == 0.0
    assert "seed" in sent
    assert sent["response_format"]["json_schema"]["strict"] is True


async def test_a_schema_violation_is_retried_with_the_error_fed_back():
    client, stub = client_with(
        [
            '{"pattern":"not_a_real_pattern","claims":[],"summary":""}',
            '{"pattern":"out_of_region_use","pattern_description":"","claims":[],"summary":"ok"}',
        ]
    )
    out = await client.complete(purpose="claims", system="s", user="u", schema=Assessment)
    assert out.value.pattern is Pattern.OUT_OF_REGION_USE
    assert len(stub.completions.calls) == 2
    # The second attempt carries the failure, rather than re-rolling blind.
    second = stub.completions.calls[1]["messages"]
    assert any("did not satisfy the schema" in m["content"] for m in second)


async def test_a_reply_that_never_satisfies_the_schema_raises():
    client, _ = client_with(['{"pattern":"nonsense"}'] * 6)
    with pytest.raises(StructuredOutputError, match="did not satisfy"):
        await client.complete(purpose="claims", system="s", user="u", schema=Assessment)


async def test_a_truncated_reply_is_retried_with_a_bigger_budget():
    """Truncation is not a schema violation and must not be retried as one.

    Measured against the live model: the defence agent overran 700 output
    tokens, its JSON was cut mid-string, and four identical retries burned 19k
    tokens before falling back.
    """
    client, stub = client_with(
        [
            '{"pattern":"none","pattern_description":"","claims":[],"summary":"trunc',
            '{"pattern":"none","pattern_description":"","claims":[],"summary":"ok"}',
        ]
    )
    stub.completions.finish_reasons = ["length", "stop"]
    out = await client.complete(
        purpose="claims", system="s", user="u", schema=Assessment, max_output_tokens=700
    )
    assert out.value.pattern is Pattern.NONE
    first, second = stub.completions.calls
    assert second["max_completion_tokens"] == 1400, "the budget doubles"
    # And it does NOT feed back a schema error, because the schema was fine.
    assert len(second["messages"]) == len(first["messages"])


async def test_an_api_failure_becomes_the_systems_own_error_type():
    from openai import APIError

    class _Boom(APIError):
        def __init__(self) -> None:
            super().__init__("upstream exploded", request=None, body=None)  # type: ignore[arg-type]

    client, _ = client_with([_Boom()] * 6)
    client.settings.openai_max_retries = 0
    with pytest.raises(LlmUnavailable):
        await client.complete(purpose="claims", system="s", user="u", schema=Assessment)


# ── per-run metering ─────────────────────────────────────────────────────────


def test_a_reading_is_a_frozen_copy_not_a_live_view():
    meter = CostMeter()
    meter.charge("gpt-5.4-mini", 100, 50)
    before = meter.reading()
    meter.charge("gpt-5.4-mini", 200, 100)
    assert before.tokens == 150, "the reading moved with the meter"
    assert meter.tokens == 450


def test_a_runs_budget_reports_its_own_spend_not_the_clients():
    """One LlmClient serves a twenty-case batch; each run reports only its own.

    The regression this pins: `answer.tokens` read the client-lifetime meter,
    so the first benchmark case reported 7,541 tokens and the twentieth
    reported 122,847 — the batch total, not the case's. It also meant the
    per-run token ceiling tripped two thirds of the way through a batch.
    """
    from sentinel.agents.budget import RunBudget

    settings_obj = settings()
    meter = CostMeter()

    meter.charge("gpt-5.4-mini", 4_000, 1_000)  # an earlier case in the batch
    first_done = meter.tokens

    budget = RunBudget(settings=settings_obj, meter=meter)
    assert budget.tokens == 0, "a fresh run starts at zero, whatever came before"

    meter.charge("gpt-5.4-mini", 900, 100)  # this run's only call
    assert budget.tokens == 1_000
    assert budget.llm_calls == 1
    assert budget.snapshot().tokens == 1_000
    assert meter.tokens == first_done + 1_000, "the client total still accumulates"


def test_the_token_ceiling_is_per_run_not_per_client():
    from sentinel.agents.budget import BudgetGuard, RunBudget
    from sentinel.domain.errors import BudgetExceeded

    settings_obj = settings()
    meter = CostMeter()
    # Two prior cases have already spent more than a single run may.
    meter.charge("gpt-5.4-mini", settings_obj.max_tokens_per_run, 0)

    guard = BudgetGuard(RunBudget(settings=settings_obj, meter=meter))
    guard.check_spend()  # must not raise: this run has spent nothing

    meter.charge("gpt-5.4-mini", settings_obj.max_tokens_per_run, 0)
    with pytest.raises(BudgetExceeded):
        guard.check_spend()


def test_a_subscription_can_go_in_the_brokers_set():
    """The broker keeps subscribers in a set, so they have to be hashable.

    A dataclass with the default ``eq=True`` has ``__hash__`` set to None, so
    every attempt to open an SSE stream raised `TypeError: unhashable type`
    from inside the response body iterator — after the 200 and the headers had
    already gone out, which is why it looked like an empty stream rather than
    an error.
    """
    from api.sse.journal import SseBroker

    broker = SseBroker(queue_size=4)
    first = broker.subscribe("run_1")
    second = broker.subscribe("run_1")
    assert broker.listeners("run_1") == 2
    assert first != second, "two connections are never the same subscription"
    broker.unsubscribe(first)
    assert broker.listeners("run_1") == 1
