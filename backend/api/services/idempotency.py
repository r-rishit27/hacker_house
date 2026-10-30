"""Retry safety: the same key twice is one side effect and two answers.

A console button that double-fires, a proxy that retries a POST, an analyst
who reloads after a timeout — all three arrive as a second identical request,
and the second one must not block a second card. The ``Idempotency-Key``
header makes a mutation replayable; this module is what makes the header mean
something.

The design is the reserve-then-complete one, not the write-it-afterwards one,
because the interesting race is the double-click and it happens *while* the
first call is still in flight. A key is inserted before the work starts:

- key unseen            -> reserved, the caller proceeds
- key seen, same request, finished   -> the stored response, replayed
- key seen, same request, in flight  -> 409 ``idempotency_in_flight``
- key seen, different request        -> 409 ``idempotency_key_reuse``

A key whose work failed is *released*, not left behind: a poisoned key that
can never be retried is worse than no idempotency at all.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from api.errors import conflict
from api.store.models import IdempotencyRecord
from api.store.session import Database

#: ``response_status`` on a reserved-but-unfinished record. Not a real HTTP
#: status, which is exactly why it is safe to use as the in-flight marker.
IN_FLIGHT = 0


class IdempotencyService:
    """Stores one response per (key, scope) and replays it.

    Responsibility: the reservation lifecycle and the request fingerprint. It
    does not know what a response means, and never calls a handler.
    Collaborators: ``Database``; ``ActionExecutionService``, which reserves
    before dispatching and completes after.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def fingerprint(request: Mapping[str, Any]) -> str:
        """A stable hash of the request body this key is bound to.

        Sorted keys and a compact separator, so two bodies that differ only in
        whitespace or field order are the same request — which is what the
        client means when it retries.
        """
        canonical = json.dumps(
            request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def reserve(self, key: str, scope: str, request_hash: str) -> IdempotencyRecord | None:
        """Claim the key, or hand back the finished response to replay.

        ``None`` means the caller owns the key and should do the work.
        """
        record = IdempotencyRecord(
            key=key,
            scope=scope,
            request_hash=request_hash,
            response_status=IN_FLIGHT,
            response_body={},
        )
        try:
            async with self._db.session() as session:
                session.add(record)
        except IntegrityError:
            # The primary key is (key, scope), so this is the second arrival —
            # the common case for a double-click, not an error.
            return await self._existing(key, scope, request_hash)
        return None

    async def complete(
        self,
        key: str,
        scope: str,
        *,
        status: int,
        body: Mapping[str, Any],
        execution_id: str | None = None,
    ) -> None:
        """Turn the reservation into a replayable response."""
        async with self._db.session() as session:
            record = await session.get(IdempotencyRecord, (key, scope))
            if record is None:
                # Nothing reserved this key: a caller that completes without
                # reserving would otherwise store a record no replay can trust.
                raise conflict(
                    f"idempotency key '{key}' was completed without being reserved",
                    code="idempotency_not_reserved",
                    key=key,
                    scope=scope,
                )
            record.response_status = status
            record.response_body = dict(body)
            record.execution_id = execution_id

    async def release(self, key: str, scope: str) -> None:
        """Drop a reservation whose work did not finish, so a retry can run.

        Only an unfinished reservation is dropped. A completed response is
        never deleted here — that would silently turn a replay into a second
        execution.
        """
        async with self._db.session() as session:
            await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.key == key,
                    IdempotencyRecord.scope == scope,
                    IdempotencyRecord.response_status == IN_FLIGHT,
                )
            )

    async def stored(self, key: str, scope: str) -> IdempotencyRecord | None:
        """The record for this key, finished or not. For diagnostics and tests."""
        async with self._db.session() as session:
            return await session.get(IdempotencyRecord, (key, scope))

    async def _existing(self, key: str, scope: str, request_hash: str) -> IdempotencyRecord:
        async with self._db.session() as session:
            record = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.key == key, IdempotencyRecord.scope == scope
                )
            )
        if record is None:
            # The row vanished between the failed insert and this read — only
            # possible if another request released it, which means the work it
            # guarded did not happen and this caller may try again.
            raise conflict(
                f"idempotency key '{key}' was released while this request was starting",
                code="idempotency_in_flight",
                key=key,
                scope=scope,
            )
        if record.request_hash != request_hash:
            raise conflict(
                f"idempotency key '{key}' was already used for a different request",
                code="idempotency_key_reuse",
                key=key,
                scope=scope,
            )
        if record.response_status == IN_FLIGHT:
            raise conflict(
                f"idempotency key '{key}' is still being processed",
                code="idempotency_in_flight",
                key=key,
                scope=scope,
            )
        return record
