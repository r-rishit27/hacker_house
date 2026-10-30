"""The shape every LLM-bearing agent takes.

Four agents in the system reach a model: the planner, the assessor, the defence
and the narrator. Each declares a Pydantic schema, renders a prompt from facts,
and — crucially — has a deterministic fallback. A model that is rate-limited,
slow or confused must degrade the investigation, not end it: the twenty answer
files are the deliverable, and a run that dies on an API hiccup produces none.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

from sentinel.agents.context import InvestigationContext
from sentinel.domain.errors import LlmUnavailable, StructuredOutputError
from sentinel.llm.client import LlmClient

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class LlmAgent(ABC, Generic[T]):
    """One model-bearing step, with a fallback that needs no model.

    Responsibility: render, call, validate, and degrade. Subclasses supply the
    schema, the two prompt halves and the fallback value.
    Collaborators: ``LlmClient`` for the call, ``BudgetGuard`` for the ceilings,
    the context's emitter for the ``llm.completed`` event.
    """

    llm: LlmClient

    purpose: str = "agent"
    schema: type[BaseModel] = BaseModel
    system: str = ""
    max_output_tokens: int = 1_200

    @abstractmethod
    def render(self, ctx: InvestigationContext) -> str:
        """The user half of the prompt: the facts, and nothing but the facts."""

    @abstractmethod
    def fallback(self, ctx: InvestigationContext) -> T:
        """What this step produces when no model answers. Never raises."""

    async def run(self, ctx: InvestigationContext) -> T:
        try:
            ctx.guard.check_spend()
        except Exception:  # noqa: BLE001 - any budget refusal means: do not call
            logger.warning("%s: budget exhausted before the call; using the fallback", self.purpose)
            return self.fallback(ctx)

        try:
            response = await self.llm.complete(
                purpose=self.purpose,
                system=self.system,
                user=self.render(ctx),
                schema=self.schema,
                max_output_tokens=self.max_output_tokens,
            )
        except (LlmUnavailable, StructuredOutputError) as exc:
            # Degrade, loudly. The trace shows the step ran without a model.
            logger.warning("%s: %s; using the fallback", self.purpose, exc)
            ctx.emitter.emit(
                "llm.completed",
                {
                    "purpose": self.purpose,
                    "model": self.llm.model,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "usd": 0.0,
                    "elapsed_s": 0.0,
                    "fallback": True,
                    "error": str(exc),
                },
                step=ctx.counter.current,
            )
            return self.fallback(ctx)

        ctx.emitter.emit(
            "llm.completed",
            {
                "purpose": self.purpose,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.total_tokens,
                "usd": round(response.usd, 5),
                "elapsed_s": round(response.elapsed_s, 2),
                "fallback": False,
            },
            step=ctx.counter.current,
        )
        ctx.emitter.emit(
            "budget.updated", ctx.budget.snapshot().as_dict(), step=ctx.counter.current
        )
        return response.value  # type: ignore[return-value]


def bullet(lines: list[str]) -> str:
    """Facts as a list the model cannot mistake for prose to paraphrase."""
    return "\n".join(f"- {line}" for line in lines if line)
