# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Explicit, idempotent continuation of selected terminal learning candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from powercontext.builtin.persistence.dream import database_now
from powercontext.builtin.trace_learning.models import (
    TRACE_LEARNING_BINDING,
    TRACE_LEARNING_PROMPT_VERSION,
    LearningResume,
    LearningRun,
    ResumeLearningRunRequest,
    TraceLearningError,
)

if TYPE_CHECKING:
    from powercontext.builtin.trace_learning.service import TraceLearningService

_RESUMABLE_REASONS = frozenset({
    "review_unresolved",
    "validation_failed",
    "invalid_generation_output",
    "inference_unavailable",
    "budget_exceeded",
    "input_budget_exceeded",
    "unavailable_tool_dependency",
})


async def resume_learning_run(  # noqa: C901 - explicit authorization, idempotency and fenced recovery preconditions
    service: TraceLearningService,
    scope_id: str,
    principal_id: str,
    run_id: str,
    request: ResumeLearningRunRequest,
) -> LearningRun:
    await service._authorize(scope_id, principal_id, "contribute")
    async with service._transaction() as connection:
        await service.intents.ensure(connection, scope_id, TRACE_LEARNING_BINDING)
        await service.intents.load(connection, scope_id, TRACE_LEARNING_BINDING, for_update=True)
        record = await service.repository.get(connection, scope_id, run_id)
        if record.principal_id != principal_id:
            raise TraceLearningError("access_revoked")
        for event in record.resume_history:
            if event.request.idempotency_key == request.idempotency_key:
                if event.request != request:
                    raise TraceLearningError("idempotency_conflict")
                return record.run
        if not record.run.terminal:
            raise TraceLearningError("learning_run_active")
        if not service.enabled or service.generator is None:
            raise TraceLearningError("capability_unavailable")
        if not record.candidate_plan_ready or len(record.resume_history) >= 64:
            raise TraceLearningError("invalid_resume_checkpoint")
        if not request.use_current_configuration and (
            record.run.prompt_version != TRACE_LEARNING_PROMPT_VERSION
            or record.run.model_config_id != service.generator.config_id
        ):
            raise TraceLearningError("resume_configuration_changed")
        if await service.repository.pending_count(connection, scope_id) >= service.budget.max_pending_per_scope:
            raise TraceLearningError("capacity_exceeded")
        selected = {(item.family, item.key) for item in request.candidates}
        known = {(item.spec.family, item.spec.key) for item in record.candidates}
        if not selected <= known:
            raise TraceLearningError("unknown_resume_candidate")
        budget = record.run.budget
        calls = budget.max_model_calls + request.additional_model_calls
        if calls > 1024:
            raise TraceLearningError("resume_budget_exceeded")
        candidates, outcomes = [], []
        for candidate in record.candidates:
            if (candidate.spec.family, candidate.spec.key) not in selected:
                candidates.append(candidate)
                continue
            if candidate.outcome.status not in {"deferred", "rejected"} or (
                candidate.outcome.reason not in _RESUMABLE_REASONS
            ):
                raise TraceLearningError("candidate_not_resumable")
            outcomes.append(candidate.outcome)
            repair_limit = (
                budget.max_candidate_repair_rounds if candidate.repair_limit is None else candidate.repair_limit
            ) + request.additional_repair_rounds
            validation_limit = (
                budget.max_candidate_validation_repairs
                if candidate.validation_repair_limit is None
                else candidate.validation_repair_limit
            ) + request.additional_repair_rounds
            if max(repair_limit, validation_limit) > 64:
                raise TraceLearningError("resume_budget_exceeded")
            # Legacy checkpoints still retain revisions and review status. Revalidate
            # their last revision rather than rediscovering or replacing the candidate.
            status = candidate.resume_status or ("reviewing" if candidate.revisions else "planned")
            candidates.append(
                candidate.model_copy(
                    update={
                        "outcome": candidate.outcome.model_copy(update={"status": status, "reason": None}),
                        "repair_limit": repair_limit,
                        "validation_repair_limit": validation_limit,
                    }
                )
            )
        now = await database_now(connection)
        intent = await service.intents.request(connection, scope_id, TRACE_LEARNING_BINDING)
        updated = record.model_copy(
            update={
                "generation": record.generation + 1,
                "request_generation": intent.requested_generation,
                "deadline_at": None,
                "candidates": tuple(candidates),
                "resume_history": (
                    *record.resume_history,
                    LearningResume(
                        request=request,
                        principal_id=principal_id,
                        accepted_at=now,
                        outcomes=tuple(outcomes),
                        usage=record.run.usage,
                        error=record.run.error,
                        prompt_version=record.run.prompt_version,
                        model_config_id=record.run.model_config_id,
                    ),
                ),
                "run": record.run.model_copy(
                    update={
                        "status": "queued",
                        "stage": "reviewing",
                        "completed_at": None,
                        "error": None,
                        "resume_count": record.run.resume_count + 1,
                        "prompt_version": TRACE_LEARNING_PROMPT_VERSION,
                        "model_config_id": service.generator.config_id,
                        "candidate_outcomes": tuple(item.outcome for item in candidates),
                        "budget": budget.model_copy(
                            update={"max_model_calls": calls, "timeout_seconds": request.timeout_seconds}
                        ),
                    }
                ),
            }
        )
        await service.repository.resume(connection, record, updated)
        return updated.run
