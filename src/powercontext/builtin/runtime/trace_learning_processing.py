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

"""Reconstruct trace-learning resources in an existing Supervisor spawn Worker."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, nullcontext
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.trace_learning.generation import open_trace_learning_generator
from powercontext.builtin.trace_learning.service import TraceLearningService

if TYPE_CHECKING:
    from powercontext.builtin.runtime.relational import RelationalContexts
    from powercontext.builtin.trace_learning.generation import TraceLearningGenerator
    from powercontext.server.processing_security import WorkerSecurity


class TraceLearningWorkerSpec(BaseModel):
    config: BuiltinConfig = Field(repr=False)
    worker_security: dict[str, Any] | None = Field(default=None, repr=False)


def run_trace_learning_worker(
    spec: TraceLearningWorkerSpec,
    assignment: ArtifactProcessingWorkAssignment,
    /,
) -> ArtifactProcessingWorkerCompletion:
    return asyncio.run(_run_trace_learning_worker(spec, assignment))


async def _run_trace_learning_worker(
    spec: TraceLearningWorkerSpec,
    assignment: ArtifactProcessingWorkAssignment,
) -> ArtifactProcessingWorkerCompletion:
    from powercontext.builtin.runtime.composition import (
        _embedding_models,
        _usage_reporting_embedding_model,
        open_builtin_contexts,
    )

    async with AsyncExitStack() as resources:
        embedding, _ = await _embedding_models(spec.config.inference, resources, None)
        contexts = await resources.enter_async_context(
            open_builtin_contexts(
                spec.config,
                embedding_model=_usage_reporting_embedding_model(embedding),
                _topic_memory_worker=True,
            )
        )
        security = None
        if spec.worker_security is not None:
            from powercontext.server.processing_security import open_worker_security

            security = await resources.enter_async_context(
                open_worker_security(spec.worker_security, contexts.database)
            )
        generator = await open_trace_learning_generator(
            spec.config.inference,
            spec.config.runtime.trace_learning_budget,
            resources,
        )
        return await process_trace_learning_invocation(
            contexts,
            assignment,
            config=spec.config,
            generator=generator,
            security=security,
        )


async def process_trace_learning_invocation(
    contexts: RelationalContexts,
    assignment: ArtifactProcessingWorkAssignment,
    *,
    config: BuiltinConfig,
    generator: TraceLearningGenerator | None,
    security: WorkerSecurity | None = None,
) -> ArtifactProcessingWorkerCompletion:
    access = None
    if security is not None:
        from powercontext.server.trace_learning_access import TraceLearningAccess

        access = TraceLearningAccess(security.access)
    service = TraceLearningService(
        contexts=contexts,
        generator=generator,
        budget=config.runtime.trace_learning_budget,
        enabled=config.runtime.trace_learning_enabled,
        authorize=None if access is None else access.authorize,
        authorization_context=nullcontext if access is None else access.access.defer_decision_audit,
        attest_artifact=None if access is None else access.attest_artifact,
    )
    try:
        await service.execute(ScopeInvocation(assignment))
    except InvocationAlreadyHandled:
        return ArtifactProcessingWorkerCompletion()
    except ArtifactProcessingLeadershipLostError:
        return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST)
    return ArtifactProcessingWorkerCompletion()
