"""The model boundary: one call, one schema, one usage record.

Everything the model produces enters the system through this class, and it
enters typed. There is no path by which a free-form completion becomes an action
name, an id or an amount — the caller passes a Pydantic model and gets that
model back or an error, and the seven prose strings the answer file allows are
the only ``str`` fields those models carry.

``answer.tokens`` is a graded field. The hand-built golden fixture reports 0
because no model ran; an agent run that reported 0 would be an obvious tell. The
meter here is where the real number comes from.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Generic, TypeVar, cast

from openai import APIError, APIStatusError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from sentinel.config.settings import Settings
from sentinel.domain.errors import LlmUnavailable, StructuredOutputError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: How far an output budget may be doubled when a reply is truncated. Past this
#: the prompt is asking for too much and the answer would not be readable anyway.
MAX_OUTPUT_CEILING = 4_000

#: Per-million-token prices, USD. Unknown models cost 0 rather than guessing —
#: a wrong price is worse than an absent one, because the budget guard trusts it.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.25, 2.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5.4-nano": (0.05, 0.40),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-3-small": (0.02, 0.0),
}


@dataclass(frozen=True, slots=True)
class MeterReading:
    """The meter's totals at one instant.

    Exists so a per-run budget can be a *delta* against a client-lifetime
    meter. One ``LlmClient`` serves a twenty-case batch, so a run that read the
    meter directly would report every token the batch had spent so far — which
    is what `answer.tokens` did, climbing from 7,541 on the first case to
    122,847 on the last, and tripping the per-run ceiling two thirds of the way
    through.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class CostMeter:
    """Running token and dollar totals, for the life of one ``LlmClient``.

    Collaborators: ``LlmClient`` charges it; ``RunBudget`` takes a baseline
    reading and reports the difference, which is what the answer file and the
    budget ceilings both use.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    calls: int = 0

    @property
    def tokens(self) -> int:
        """Every token this client has spent, across every run it served."""
        return self.prompt_tokens + self.completion_tokens

    def reading(self) -> MeterReading:
        """The totals right now, frozen."""
        return MeterReading(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            usd=self.usd,
            calls=self.calls,
        )

    def charge(self, model: str, prompt: int, completion: int) -> float:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1
        cost = price_of(model, prompt, completion)
        self.usd += cost
        return cost


def price_of(model: str, prompt: int, completion: int) -> float:
    """USD for one call. Falls back to the base id of a dated snapshot."""
    rates = PRICING.get(model)
    if rates is None:
        # "gpt-5.4-mini-2026-03-17" prices as "gpt-5.4-mini".
        for known, known_rates in PRICING.items():
            if model.startswith(known):
                rates = known_rates
                break
    if rates is None:
        return 0.0
    prompt_rate, completion_rate = rates
    return (prompt * prompt_rate + completion * completion_rate) / 1_000_000


@dataclass(frozen=True, slots=True)
class LlmResponse(Generic[T]):
    """One structured completion, with what it cost."""

    value: T
    model: str
    purpose: str
    prompt_tokens: int
    completion_tokens: int
    usd: float
    elapsed_s: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LlmClient:
    """Structured completion against OpenAI, with retries and accounting.

    Responsibility: render a prompt, demand a schema, validate the reply, record
    the cost. It knows nothing about fraud; callers pass a rendered prompt and a
    Pydantic model.
    Collaborators: ``Settings`` for the model and credential, ``CostMeter`` for
    the totals, and the agents in ``sentinel.agents`` as callers.

    Two model-specific facts, both measured against this account rather than
    assumed: ``gpt-5.4-mini`` accepts ``temperature`` and ``seed``, and it
    rejects ``max_tokens`` in favour of ``max_completion_tokens``.
    """

    settings: Settings
    meter: CostMeter = field(default_factory=CostMeter)
    client: AsyncOpenAI | None = None
    #: Temperature 0 and a fixed seed, so two runs of the benchmark agree.
    temperature: float = 0.0
    seed: int = 20_261_221

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = AsyncOpenAI(
                api_key=self.settings.openai_api_key.get_secret_value(),
                timeout=self.settings.openai_timeout_s,
                max_retries=0,  # retries are handled here, so failures are logged
            )

    @property
    def model(self) -> str:
        return self.settings.openai_model

    async def complete(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[T],
        max_output_tokens: int = 1_200,
    ) -> LlmResponse[T]:
        """One structured call. Returns ``schema`` or raises.

        A schema violation is retried once with the validation error fed back,
        because the usual cause is a missing field rather than a confused model.
        """
        assert self.client is not None
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None

        for attempt in range(1 + self.settings.openai_max_retries):
            started = perf_counter()
            try:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    # The SDK types messages as a union of TypedDicts; this list is
                    # built here and is always well-formed.
                    messages=cast("Any", messages),
                    temperature=self.temperature,
                    seed=self.seed,
                    # NOT max_tokens: this model family rejects it outright.
                    max_completion_tokens=max_output_tokens,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": purpose,
                            "schema": json_schema_for(schema),
                            "strict": True,
                        },
                    },
                )
            except RateLimitError as exc:
                last_error = exc
                await self._backoff(attempt, purpose, "rate limited")
                continue
            except (APIStatusError, APIError) as exc:
                last_error = exc
                if attempt >= self.settings.openai_max_retries:
                    raise LlmUnavailable(
                        f"{purpose}: {type(exc).__name__}: {exc}", purpose=purpose
                    ) from exc
                await self._backoff(attempt, purpose, type(exc).__name__)
                continue

            elapsed = perf_counter() - started
            usage = completion.usage
            prompt_tokens = usage.prompt_tokens if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0
            cost = self.meter.charge(self.model, prompt_tokens, completion_tokens)

            choice = completion.choices[0]
            content = choice.message.content or ""

            # Truncation is not a schema problem and must not be retried as one.
            # Measured: the exoneration agent overran 700 output tokens, the JSON
            # was cut mid-string, and four identical retries burned 19k tokens
            # before falling back. Give it room instead of asking again.
            if choice.finish_reason == "length" and attempt < self.settings.openai_max_retries:
                max_output_tokens = min(max_output_tokens * 2, MAX_OUTPUT_CEILING)
                logger.warning(
                    "%s: reply hit the output ceiling; retrying with %d tokens",
                    purpose,
                    max_output_tokens,
                )
                continue

            try:
                value = schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = exc
                logger.warning("%s: schema violation on attempt %d", purpose, attempt + 1)
                if attempt >= self.settings.openai_max_retries:
                    raise StructuredOutputError(
                        f"{purpose}: reply did not satisfy {schema.__name__}: {exc}",
                        purpose=purpose,
                    ) from exc
                # Feed the error back rather than re-rolling blind.
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That reply did not satisfy the schema. Fix exactly these "
                            f"problems and reply again:\n{exc}"
                        ),
                    }
                )
                continue

            logger.info(
                "llm %s model=%s tokens=%d+%d usd=%.4f in %.2fs",
                purpose,
                self.model,
                prompt_tokens,
                completion_tokens,
                cost,
                elapsed,
            )
            return LlmResponse(
                value=value,
                model=self.model,
                purpose=purpose,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                usd=cost,
                elapsed_s=elapsed,
            )

        raise LlmUnavailable(f"{purpose}: exhausted retries: {last_error}", purpose=purpose)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch at the configured dimension.

        256 dimensions rather than the model's full width: the ClosedCase
        write-back is ~25 MB at 256 and ~130 MB at 1536, and `-3-large`
        truncated to 256 retains more quality than `-3-small` at the same width.
        """
        assert self.client is not None
        if not texts:
            return []
        try:
            response = await self.client.embeddings.create(
                model=self.settings.openai_embedding_model,
                input=texts,
                dimensions=self.settings.openai_embedding_dimensions,
            )
        except (APIStatusError, APIError) as exc:
            raise LlmUnavailable(f"embeddings: {exc}") from exc
        self.meter.charge(
            self.settings.openai_embedding_model,
            response.usage.total_tokens if response.usage else 0,
            0,
        )
        return [item.embedding for item in response.data]

    async def _backoff(self, attempt: int, purpose: str, reason: str) -> None:
        delay = 2.0 * (2**attempt)
        logger.warning("%s: %s, retrying in %.0fs", purpose, reason, delay)
        await asyncio.sleep(delay)


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """A Pydantic model as an OpenAI *strict* JSON schema.

    Strict mode is narrower than JSON Schema: every property must be required,
    ``additionalProperties`` must be false, and ``$ref``/``$defs`` are not
    accepted, so definitions are inlined.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return cast("dict[str, Any]", _strictify(_inline(schema, defs)))


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.split("/")[-1], {})
            merged = {k: v for k, v in node.items() if k != "$ref"}
            return _inline({**target, **merged}, defs)
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    return node


def _strictify(node: Any) -> Any:
    if isinstance(node, dict):
        out = {k: _strictify(v) for k, v in node.items()}
        if out.get("type") == "object":
            # Strict mode closes every object, including one with no declared
            # properties -- which is why a free-form dict cannot be expressed
            # and must not appear in an agent's schema.
            out["additionalProperties"] = False
            if "properties" in out:
                out["required"] = list(out["properties"])
        # Pydantic emits these; strict mode rejects them.
        for unsupported in ("default", "title", "examples"):
            out.pop(unsupported, None)
        return out
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    return node


def load_json(content: str) -> dict[str, Any]:
    """Parse a model reply, raising the system's own error type."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"reply was not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StructuredOutputError(f"reply was {type(parsed).__name__}, not an object")
    return parsed
