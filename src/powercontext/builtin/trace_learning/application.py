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

"""Scope-bound trace import and status operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.trace_learning.models import (
    TRACE_LEARNING_BINDING,
    GetLearningRunRequest,
    ImportTraceLearningRequest,
    LearningRun,
    TraceLearningError,
)

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.trace_learning.service import TraceLearningService


class ScopedTraceLearningApplication:
    def __init__(self, runtime: BuiltinRuntime, scope_id: str, principal_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)
        self.principal_id = principal_id

    def _service(self) -> TraceLearningService:
        if self._runtime._trace_learning_service is None:
            raise TraceLearningError("capability_unavailable")
        return self._runtime._trace_learning_service

    async def import_traces(self, request: ImportTraceLearningRequest, /) -> LearningRun:
        async with self._runtime._scoped_operation(self.scope_id):
            run = await self._service().import_traces(self.scope_id, self.principal_id, request)
        if not run.terminal and self._runtime.artifact_processing_supervisor is not None:
            self._runtime.artifact_processing_supervisor.wake(TRACE_LEARNING_BINDING)
        return run

    async def get(self, request: GetLearningRunRequest, /) -> LearningRun:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().get_run(self.scope_id, self.principal_id, request.run_id)


class TraceLearningApplication:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /, *, principal_id: str = "runtime") -> ScopedTraceLearningApplication:
        return ScopedTraceLearningApplication(self._runtime, scope_id, principal_id)
