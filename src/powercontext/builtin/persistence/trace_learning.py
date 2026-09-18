# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable trace import idempotency, worker ownership and bounded learned manifests."""

from __future__ import annotations

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.dream import ticks
from powercontext.builtin.persistence.tables import TRACE_LEARNING_RUNS_TABLE as RUNS
from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningRecord, TraceLearningError


def _payload(record: LearningRecord) -> bytes:
    return dump_model(record, kind="trace_learning", name="record")


def _decode(value: object) -> LearningRecord:
    return load_model(
        LearningRecord, stored_bytes(value, column="trace_learning"), kind="trace_learning", name="record"
    )


class TraceLearningRepository:
    async def find_request(
        self,
        connection: AsyncConnection,
        scope_id: str,
        principal_id: str,
        request: ImportTraceLearningRequest,
    ) -> LearningRecord | None:
        value = await connection.scalar(
            select(RUNS.c.payload).where(
                RUNS.c.scope_id == scope_id,
                RUNS.c.principal_key == content_digest(principal_id.encode())[7:],
                RUNS.c.idempotency_key == request.idempotency_key,
            )
        )
        if value is None:
            return None
        record = _decode(value)
        if record.request.digest() != request.digest():
            raise TraceLearningError("idempotency_conflict")
        return record

    async def create(self, connection: AsyncConnection, record: LearningRecord) -> None:
        run = record.run
        await connection.execute(
            insert(RUNS).values(
                scope_id=run.scope_id,
                run_id=run.run_id,
                principal_key=content_digest(record.principal_id.encode())[7:],
                idempotency_key=record.request.idempotency_key,
                request_digest=record.request.digest(),
                status=run.status,
                accepted_at=ticks(run.accepted_at),
                generation=record.generation,
                request_generation=record.request_generation,
                payload=_payload(record),
            )
        )

    async def get(self, connection: AsyncConnection, scope_id: str, run_id: str) -> LearningRecord:
        value = await connection.scalar(
            select(RUNS.c.payload).where(
                RUNS.c.scope_id == scope_id,
                RUNS.c.run_id == run_id,
            )
        )
        if value is None:
            raise TraceLearningError("learning_run_not_found")
        return _decode(value)

    async def pending_count(self, connection: AsyncConnection, scope_id: str) -> int:
        return int(
            await connection.scalar(
                select(func.count())
                .select_from(RUNS)
                .where(
                    RUNS.c.scope_id == scope_id,
                    RUNS.c.status.in_(("queued", "running")),
                )
            )
            or 0
        )

    async def next_pending(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        through_generation: int | None = None,
    ) -> LearningRecord | None:
        query = select(RUNS.c.payload).where(RUNS.c.scope_id == scope_id, RUNS.c.status.in_(("queued", "running")))
        if through_generation is not None:
            query = query.where(RUNS.c.request_generation <= through_generation)
        value = await connection.scalar(query.order_by(RUNS.c.request_generation, RUNS.c.accepted_at).limit(1))
        return None if value is None else _decode(value)

    async def list_completed(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        limit: int | None = 30,
        include_partial: bool = False,
    ) -> tuple[LearningRecord, ...]:
        """Page through completed runs; an explicit limit bounds the caller's catalog, not recall."""
        if limit is not None and limit <= 0:
            return ()
        result = []
        cursor = None
        while limit is None or len(result) < limit:
            query = select(RUNS.c.payload, RUNS.c.accepted_at, RUNS.c.run_id).where(RUNS.c.scope_id == scope_id)
            if not include_partial:
                query = query.where(RUNS.c.status == "succeeded")
            if cursor is not None:
                query = query.where(
                    or_(
                        RUNS.c.accepted_at < cursor[0],
                        and_(
                            RUNS.c.accepted_at == cursor[0],
                            RUNS.c.run_id < cursor[1],
                        ),
                    )
                )
            size = 100 if limit is None else min(100, limit - len(result))
            rows = (
                await connection.execute(query.order_by(RUNS.c.accepted_at.desc(), RUNS.c.run_id.desc()).limit(size))
            ).all()
            if not rows:
                break
            for row in rows:
                record = _decode(row.payload)
                if record.run.status == "succeeded" or (
                    include_partial and record.candidate_plan_ready and record.run.artifacts
                ):
                    result.append(record)
            cursor = rows[-1].accepted_at, rows[-1].run_id
        return tuple(result)

    async def claim(self, connection: AsyncConnection, record: LearningRecord) -> LearningRecord:
        claimed = record.model_copy(update={"generation": record.generation + 1})
        result = await connection.execute(
            update(RUNS)
            .where(
                RUNS.c.scope_id == record.run.scope_id,
                RUNS.c.run_id == record.run.run_id,
                RUNS.c.generation == record.generation,
                RUNS.c.status.in_(("queued", "running")),
            )
            .values(generation=claimed.generation, status=claimed.run.status, payload=_payload(claimed))
        )
        if result.rowcount != 1:
            raise TraceLearningError("attempt_conflict")
        return claimed

    async def store(self, connection: AsyncConnection, record: LearningRecord) -> None:
        result = await connection.execute(
            update(RUNS)
            .where(
                RUNS.c.scope_id == record.run.scope_id,
                RUNS.c.run_id == record.run.run_id,
                RUNS.c.generation == record.generation,
                RUNS.c.status == "running",
            )
            .values(status=record.run.status, payload=_payload(record))
        )
        if result.rowcount != 1:
            raise TraceLearningError("attempt_conflict")
