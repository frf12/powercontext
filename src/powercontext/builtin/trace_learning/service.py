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

"""Import-only trace learning on shared Artifact storage and Supervisor invocations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import sqlglot
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceDraft
from powercontext.builtin.artifacts.skill import Skill, SkillDraft, SkillPackageError, build_instruction_skill_package
from powercontext.builtin.artifacts.tool import Tool, ToolDraft, render_tool_sql
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.inference.errors import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.persistence.dream import database_now
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, SCOPES_TABLE
from powercontext.builtin.persistence.trace_learning import TraceLearningRepository
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
from powercontext.builtin.trace_learning.models import (
    TRACE_LEARNING_BINDING,
    TRACE_LEARNING_PROMPT_VERSION,
    CompleteTrace,
    GeneratedTraceLearningBundle,
    ImportTraceLearningRequest,
    LearningBudget,
    LearningRecord,
    LearningRun,
    PreviousLearningArtifact,
    RejectedLearningCandidate,
    ResolvedToolTraceCall,
    SQLMismatchError,
    SQLMismatchFeedback,
    ToolTraceExample,
    TraceLearningError,
    TraceLearningGenerationInput,
    TraceLearningHostProfile,
    ValidationReport,
)
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.relational import RelationalContexts
    from powercontext.builtin.trace_learning.generation import TraceLearningGenerator

TraceLearningPermission = Literal["read", "contribute", "write"]
TraceLearningAuthorizer = Callable[[str, str, TraceLearningPermission, SourceRef | ArtifactRef | None], Awaitable[None]]
ArtifactAttester = Callable[[AsyncConnection, str, str, ArtifactRef], Awaitable[None]]


class TraceLearningService:
    def __init__(
        self,
        *,
        contexts: RelationalContexts,
        generator: TraceLearningGenerator | None = None,
        budget: LearningBudget | None = None,
        authorize: TraceLearningAuthorizer | None = None,
        authorization_context: Callable[[], AbstractAsyncContextManager[None]] = nullcontext,
        attest_artifact: ArtifactAttester | None = None,
        enabled: bool = True,
    ) -> None:
        self.contexts = contexts
        self.database = contexts.database
        self.generator = generator
        self.budget = budget or LearningBudget()
        self.authorize = authorize
        self.authorization_context = authorization_context
        self.attest_artifact = attest_artifact
        self.enabled = enabled
        self.repository = TraceLearningRepository()
        self.intents = ArtifactProcessingIntentRepository()

    def configure_authorization(
        self,
        authorize: TraceLearningAuthorizer,
        authorization_context: Callable[[], AbstractAsyncContextManager[None]],
        attest_artifact: ArtifactAttester | None = None,
    ) -> None:
        self.authorize = authorize
        self.authorization_context = authorization_context
        self.attest_artifact = attest_artifact

    async def _authorize(
        self,
        scope_id: str,
        principal_id: str,
        permission: TraceLearningPermission,
        ref: SourceRef | ArtifactRef | None = None,
    ) -> None:
        if self.authorize is not None:
            await self.authorize(scope_id, principal_id, permission, ref)
        elif principal_id != "runtime":
            raise TraceLearningError("access_revoked")

    @asynccontextmanager
    async def _transaction(self, invocation: ScopeInvocation | None = None) -> AsyncIterator[AsyncConnection]:
        async with self.authorization_context(), self.database.transaction() as connection:
            if invocation is not None:
                await invocation.guard(connection)
            yield connection

    async def import_traces(
        self,
        scope_id: str,
        principal_id: str,
        request: ImportTraceLearningRequest,
    ) -> LearningRun:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            existing = await self.repository.find_request(connection, scope_id, principal_id, request)
            if existing is not None:
                return existing.run
        if not self.enabled:
            raise TraceLearningError("capability_unavailable")
        await self._authorize(scope_id, principal_id, "contribute")
        async with self._transaction() as connection:
            await self.intents.ensure(connection, scope_id, TRACE_LEARNING_BINDING)
            await self.intents.load(connection, scope_id, TRACE_LEARNING_BINDING, for_update=True)
            locked = await connection.execute(
                update(SCOPES_TABLE)
                .where(SCOPES_TABLE.c.scope_id == scope_id)
                .values(
                    version=SCOPES_TABLE.c.version,
                )
            )
            if locked.rowcount != 1:
                raise TraceLearningError("scope_not_found")
            existing = await self.repository.find_request(connection, scope_id, principal_id, request)
            if existing is not None:
                return existing.run
            if await self.repository.pending_count(connection, scope_id) >= self.budget.max_pending_per_scope:
                raise TraceLearningError("capacity_exceeded")
            refs = []
            for trace in request.traces:
                payload = trace.model_dump_json()
                digest = content_digest(payload.encode())
                source = await CONTENT_SOURCE_ADAPTER.resolve(
                    ContentCapture(
                        source_id="trace-learning-" + digest[7:],
                        content=payload,
                        metadata={"trace_id": trace.trace_id, "content_digest": digest, "origin": "user_import"},
                    )
                )
                stored = await self.contexts.repositories.sources.add(connection, scope_id, source)
                refs.append(stored.ref)
            intent = await self.intents.request(connection, scope_id, TRACE_LEARNING_BINDING)
            run = LearningRun(
                scope_id=scope_id,
                run_id="lr_" + uuid4().hex,
                sources=tuple(refs),
                host_profile=request.host_profile,
                input_digest=request.digest(),
                accepted_at=await database_now(connection),
                budget=self.budget,
            )
            await self.repository.create(
                connection,
                LearningRecord(
                    run=run,
                    request=request,
                    principal_id=principal_id,
                    request_generation=intent.requested_generation,
                ),
            )
            return run

    async def get_run(self, scope_id: str, principal_id: str, run_id: str) -> LearningRun:
        await self._authorize(scope_id, principal_id, "read")
        async with self._transaction() as connection:
            return (await self.repository.get(connection, scope_id, run_id)).run

    async def learned_artifacts(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        limit: int | None = None,
        host_profile: TraceLearningHostProfile | None = None,
    ) -> tuple[Artifact[Any], ...]:
        """Read published, active learned revisions; callers apply user read authorization."""
        if limit is not None and limit <= 0:
            return ()
        result: list[Artifact[Any]] = []
        seen = set()
        for record in await self.repository.list_completed(connection, scope_id, limit=None, include_partial=True):
            if host_profile is not None and record.run.host_profile != host_profile:
                continue
            for ref in record.run.artifacts:
                key = (ref.family, ref.artifact_id)
                if key in seen:
                    continue
                state = await connection.scalar(
                    select(ARTIFACT_HEADS_TABLE.c.lifecycle_state).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                        ARTIFACT_HEADS_TABLE.c.family == ref.family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id == ref.artifact_id,
                        ARTIFACT_HEADS_TABLE.c.revision == ref.revision,
                    )
                )
                if state != "active":
                    continue
                seen.add(key)
                result.append(await self.contexts.repositories.artifacts.get(connection, scope_id, ref))
                if limit is not None and len(result) >= limit:
                    return tuple(result)
        return tuple(result)

    async def _previous(
        self, connection: AsyncConnection, record: LearningRecord
    ) -> tuple[PreviousLearningArtifact, ...]:
        records = await self.repository.list_completed(connection, record.run.scope_id, limit=None)
        keys = {}
        for previous in reversed(records):
            if previous.run.host_profile == record.run.host_profile:
                keys.update({_ref_key(ref): key.split(":", 1)[1] for key, ref in previous.artifact_keys.items()})
        values = await self.learned_artifacts(
            connection,
            record.run.scope_id,
            limit=record.run.budget.previous_artifact_limit,
            host_profile=record.run.host_profile,
        )
        result = []
        for value in values:
            if _ref_key(value.as_ref()) not in keys:
                continue
            await self._authorize(record.run.scope_id, record.principal_id, "read", value.as_ref())
            result.append(
                PreviousLearningArtifact(
                    ref=value.as_ref(),
                    key=keys[_ref_key(value.as_ref())],
                    content=value.content.model_dump(mode="json"),
                )
            )
        return tuple(result)

    async def execute(self, invocation: ScopeInvocation) -> bool:  # noqa: C901 - fenced claim and bounded failure categories
        work = invocation.assignment
        if work.binding_name != TRACE_LEARNING_BINDING or work.artifact_family != "tool":
            raise TraceLearningError("processing_binding_mismatch")
        async with self._transaction(invocation) as connection:
            await invocation.start(connection)
            record = await self.repository.next_pending(
                connection,
                work.scope_id,
                through_generation=work.claimed_request_generation,
            )
            if record is None:
                await invocation.complete(connection, remaining_work=False)
                return False
            now = await database_now(connection)
            record = record.model_copy(
                update={
                    "run": record.run.model_copy(
                        update={
                            "status": "running",
                            "started_at": record.run.started_at or now,
                            "attempt_count": record.run.attempt_count + 1,
                            "model_config_id": record.run.model_config_id
                            or (None if self.generator is None else self.generator.config_id),
                        }
                    ),
                    "deadline_at": record.deadline_at or now + timedelta(seconds=record.run.budget.timeout_seconds),
                }
            )
            record = await self.repository.claim(connection, record)
        try:
            async with self._transaction(invocation) as connection:
                if record.deadline_at is None:
                    raise TraceLearningError("invalid_checkpoint")  # noqa: TRY301 - persist malformed Run
                remaining = (record.deadline_at - await database_now(connection)).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                await self._execute_record(record, invocation)
        except (ArtifactProcessingLeadershipLostError, InvocationAlreadyHandled):
            raise
        except (InferenceTimeoutError, InferenceUnavailableError):
            await self._fail(record, invocation, "inference_unavailable", retry=True)
        except TimeoutError:
            await self._fail(record, invocation, "budget_exceeded")
        except RevisionConflictError:
            await self._fail(record, invocation, "artifact_revision_conflict")
        except TraceLearningError as error:
            await self._fail(record, invocation, error.code)
        except (InvalidInferenceOutputError, ValidationError):
            await self._fail(record, invocation, "invalid_generation_output")
        except SkillPackageError:
            await self._fail(record, invocation, "invalid_skill_package")
        except Exception:
            await self._fail(record, invocation, "learning_processing_error")
        return True

    async def _execute_record(self, record: LearningRecord, invocation: ScopeInvocation) -> None:  # noqa: C901 - bounded checkpoint stages
        run = record.run
        from powercontext.builtin.trace_learning.generation import CandidateLearningGenerator

        if not self.enabled or self.generator is None:
            raise TraceLearningError("capability_unavailable")
        allowed_config_ids = {self.generator.config_id}
        legacy = run.prompt_version == "powercontext.trace-learning.v2"
        if (
            legacy
            and isinstance(self.generator, CandidateLearningGenerator)
            and self.generator.legacy_config_id is not None
        ):
            allowed_config_ids.add(self.generator.legacy_config_id)
        if run.model_config_id not in allowed_config_ids or (
            not legacy and run.prompt_version != TRACE_LEARNING_PROMPT_VERSION
        ):
            raise TraceLearningError("capability_unavailable")
        await self._authorize(run.scope_id, record.principal_id, "contribute")
        if record.generation_input is None:
            async with self._transaction(invocation) as connection:
                previous = await self._previous(connection, record)
                for ref in run.sources:
                    await self._authorize(run.scope_id, record.principal_id, "read", ref)
                    await self.contexts.repositories.sources.get(connection, run.scope_id, ref)
                value = TraceLearningGenerationInput(
                    traces=record.request.traces,
                    host_profile=run.host_profile,
                    previous_artifacts=previous,
                    resolved_tool_calls=await self._resolve_tool_calls(connection, record),
                )
                if len(value.model_dump_json()) > run.budget.max_input_chars:
                    raise TraceLearningError("input_budget_exceeded")
                record = record.model_copy(update={"generation_input": value})
                await self.repository.store(connection, record)
        if record.generation_input is None:
            raise TraceLearningError("invalid_checkpoint")
        for resolved in record.generation_input.resolved_tool_calls:
            await self._authorize(run.scope_id, record.principal_id, "read", resolved.tool_ref)
        if isinstance(self.generator, CandidateLearningGenerator) and not legacy:
            from powercontext.builtin.trace_learning.workflow import CandidateWorkflow

            await CandidateWorkflow(self, invocation, record, self.generator).execute()
            return
        while True:
            if record.generated is None:
                record = await self._generate_candidate(record, invocation)
            if record.generated is None or record.generation_input is None:
                raise TraceLearningError("invalid_checkpoint")
            generation_input = record.generation_input
            if record.run.usage.model_calls > run.budget.max_model_calls or (
                record.generated_output_tokens is not None
                and record.generated_output_tokens > run.budget.max_output_tokens
            ):
                raise TraceLearningError("budget_exceeded")
            async with self._transaction(invocation) as connection:
                previous_runs = await self.repository.list_completed(connection, run.scope_id, limit=None)
            try:
                validation = validate_bundle(
                    record.generated,
                    record.request.traces,
                    run.host_profile,
                    previous_runs,
                    generation_input.resolved_tool_calls,
                )
            except SQLMismatchError as error:
                rejected = RejectedLearningCandidate(
                    candidate=record.generated, feedback=error.feedback, usage=record.run.usage
                )
                record = record.model_copy(
                    update={
                        "rejected_candidates": (*record.rejected_candidates, rejected),
                        "generated": None,
                        "generated_output_tokens": None,
                        "generation_input": generation_input.model_copy(update={"validation_feedback": rejected}),
                    }
                )
                # Archive the failed program and its feedback together before another dispatch.
                async with self._transaction(invocation) as connection:
                    await self.repository.store(connection, record)
                if record.run.usage.model_calls >= run.budget.max_model_calls:
                    raise
                continue
            break
        record = record.model_copy(
            update={"run": record.run.model_copy(update={"stage": "saving", "validation": validation})}
        )
        async with self._transaction(invocation) as connection:
            await self._authorize(run.scope_id, record.principal_id, "contribute")
            for resolved in generation_input.resolved_tool_calls:
                await self._authorize(run.scope_id, record.principal_id, "read", resolved.tool_ref)
            record = await self._save_bundle(connection, record)
            finished = record.run.model_copy(
                update={
                    "status": "succeeded",
                    "stage": "complete",
                    "completed_at": await database_now(connection),
                    "error": None,
                }
            )
            await self.repository.store(connection, record.model_copy(update={"run": finished}))
            await self._complete(connection, invocation)

    async def _generate_candidate(self, record: LearningRecord, invocation: ScopeInvocation) -> LearningRecord:
        run = record.run
        if self.generator is None or record.generation_input is None:
            raise TraceLearningError("invalid_checkpoint")
        if len(record.generation_input.model_dump_json()) > run.budget.max_input_chars:
            raise TraceLearningError("input_budget_exceeded")
        if record.run.usage.model_calls >= run.budget.max_model_calls:
            if record.rejected_candidates:
                raise TraceLearningError("trace_sql_mismatch")
            raise TraceLearningError("budget_exceeded")
        await self._authorize(run.scope_id, record.principal_id, "contribute")
        for ref in run.sources:
            await self._authorize(run.scope_id, record.principal_id, "read", ref)
        for artifact in record.generation_input.previous_artifacts:
            await self._authorize(run.scope_id, record.principal_id, "read", artifact.ref)
        for resolved in record.generation_input.resolved_tool_calls:
            await self._authorize(run.scope_id, record.principal_id, "read", resolved.tool_ref)
        record = record.model_copy(
            update={
                "run": record.run.model_copy(
                    update={
                        "stage": "generating",
                        "usage": record.run.usage.model_copy(
                            update={
                                "model_calls": record.run.usage.model_calls + 1,
                            }
                        ),
                    }
                )
            }
        )
        async with self._transaction(invocation) as connection:
            await self.repository.store(connection, record)
        if record.generation_input is None:
            raise TraceLearningError("invalid_checkpoint")
        generated = await self.generator.generate(record.generation_input)
        usage = record.run.usage.model_copy(
            update={
                "model_calls": record.run.usage.model_calls + max(0, generated.usage.requests - 1),
                "input_tokens": _sum_usage(record.run.usage.input_tokens, generated.usage.input_tokens),
                "output_tokens": _sum_usage(record.run.usage.output_tokens, generated.usage.output_tokens),
            }
        )
        record = record.model_copy(
            update={
                "generated": generated.output,
                "generated_output_tokens": generated.usage.output_tokens,
                "run": record.run.model_copy(update={"stage": "validating", "usage": usage}),
            }
        )
        async with self._transaction(invocation) as connection:
            await self.repository.store(connection, record)
        return record

    async def _resolve_tool_calls(
        self, connection: AsyncConnection, record: LearningRecord
    ) -> tuple[ResolvedToolTraceCall, ...]:
        resolved = []
        for trace in record.request.traces:
            for call in trace.tool_calls:
                receipt = _learned_receipt(call.result)
                if receipt is None:
                    continue
                try:
                    ref = ArtifactRef.model_validate(receipt.get("ref"))
                except ValueError as error:
                    raise TraceLearningError("invalid_learned_tool_receipt") from error
                if ref.family != "tool":
                    raise TraceLearningError("invalid_learned_tool_receipt")
                await self._authorize(record.run.scope_id, record.principal_id, "read", ref)
                # The exact historical revision in this scope is the authority for program identity.
                try:
                    tool = await self.contexts.repositories.artifacts.get(connection, record.run.scope_id, ref)
                except ArtifactNotFoundError as error:
                    raise TraceLearningError("invalid_learned_tool_receipt") from error
                if not isinstance(tool, Tool):
                    raise TraceLearningError("invalid_learned_tool_receipt")
                _validate_learned_receipt(call, receipt, tool, record.run.host_profile)
                try:
                    executed_sql = render_tool_sql(tool.content, call.arguments)
                except ValueError as error:
                    raise TraceLearningError("invalid_learned_tool_receipt") from error
                resolved.append(
                    ResolvedToolTraceCall(
                        trace_id=trace.trace_id,
                        call_id=call.call_id,
                        tool_ref=ref,
                        content=tool.content,
                        executed_sql=executed_sql,
                    )
                )
        return tuple(resolved)

    async def _save_bundle(self, connection: AsyncConnection, record: LearningRecord) -> LearningRecord:  # noqa: C901 - atomic typed bundle write
        if record.generated is None or record.generation_input is None:
            raise TraceLearningError("invalid_checkpoint")
        scope = record.run.scope_id
        repositories = self.contexts.repositories
        previous = {(item.ref.family, item.key): item.ref for item in record.generation_input.previous_artifacts}
        saved: dict[str, ArtifactRef] = dict(record.artifact_keys)

        async def save(family: str, item, content, dependencies: tuple[ArtifactRef, ...] = ()) -> Artifact[Any]:
            identity = content_digest(
                (record.run.host_profile.model_dump_json() + ":" + family + ":" + item.key).encode()
            )
            artifact_id = "tl_" + identity[7:47]
            old_ref = previous.get((family, item.key))
            if old_ref is None:
                revision = await connection.scalar(
                    select(ARTIFACT_HEADS_TABLE.c.revision).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == scope,
                        ARTIFACT_HEADS_TABLE.c.family == family,
                        ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
                    )
                )
                if revision is not None:
                    old_ref = ArtifactRef(family=family, artifact_id=artifact_id, revision=revision)
                    await self._authorize(scope, record.principal_id, "read", old_ref)
            old = None if old_ref is None else await repositories.artifacts.get(connection, scope, old_ref)
            if old is not None:
                await self._authorize(scope, record.principal_id, "write", old.as_ref())
            sources = _unique_refs((*(() if old is None else old.lineage.sources), *record.run.sources))
            lineage = _unique_refs((*(() if old is None else (old.as_ref(),)), *dependencies))
            draft_type = {"experience": ExperienceDraft, "skill": SkillDraft, "tool": ToolDraft}[family]
            draft = draft_type(content=content, sources=sources, artifacts=lineage)
            if old is None:
                artifact = await repositories.artifacts.create(connection, scope, artifact_id, draft)
            else:
                artifact = await repositories.artifacts.revise(connection, scope, old, draft)
            if self.attest_artifact is not None:
                await self.attest_artifact(connection, scope, record.principal_id, artifact.as_ref())
            if isinstance(artifact, Experience):
                await self.contexts.experience_index.replace(connection, scope, artifact)
            elif isinstance(artifact, Skill):
                if artifact.content.package is None:
                    raise TraceLearningError("invalid_skill_package")
                package = await repositories.skill_packages.get(connection, scope, artifact.content.package)
                await self.contexts.experience_index.replace_skill(connection, scope, artifact, package)
            saved[family + ":" + item.key] = artifact.as_ref()
            return artifact

        for item in record.generated.experiences:
            await save("experience", item, item.content)
        experience_refs = tuple(ref for ref in saved.values() if ref.family == "experience")
        for item in record.generated.tools:
            await save("tool", item, item.content, experience_refs)
        for item in record.generated.skills:
            dependencies = tuple(saved["tool:" + key] for key in item.tool_keys)
            content = item.content.model_copy(update={"tool_dependencies": dependencies, "package": None})
            package = build_instruction_skill_package(content)
            await repositories.skill_packages.add(connection, scope, package)
            await save("skill", item, package.as_skill_content(), (*experience_refs, *dependencies))
        return record.model_copy(
            update={
                "artifact_keys": saved,
                "run": record.run.model_copy(update={"artifacts": tuple(saved.values())}),
            }
        )

    async def _complete(self, connection: AsyncConnection, invocation: ScopeInvocation) -> None:
        work = invocation.assignment
        current = await invocation.guard(connection)
        remaining = await self.repository.next_pending(connection, work.scope_id)
        if remaining is not None and current.requested_generation == work.claimed_request_generation:
            await self.intents.request(connection, work.scope_id, work.binding_name)
        await invocation.complete(connection, remaining_work=remaining is not None)

    async def _fail(
        self, record: LearningRecord, invocation: ScopeInvocation, code: str, *, retry: bool = False
    ) -> None:
        async with self._transaction(invocation) as connection:
            current = await self.repository.get(connection, record.run.scope_id, record.run.run_id)
            if current.generation != record.generation:
                raise TraceLearningError("attempt_conflict")
            now = await database_now(connection)
            can_retry = (
                retry
                and current.run.usage.model_calls < current.run.budget.max_model_calls
                and (current.deadline_at is None or now < current.deadline_at)
            )
            partial = current.candidate_plan_ready and bool(current.run.artifacts) and not can_retry
            if current.candidate_plan_ready and not can_retry:
                current = current.model_copy(
                    update={
                        "candidates": tuple(
                            candidate
                            if candidate.outcome.status in {"published", "rejected", "deferred"}
                            else candidate.model_copy(
                                update={
                                    "outcome": candidate.outcome.model_copy(
                                        update={
                                            "status": "deferred",
                                            "reason": code,
                                        }
                                    )
                                }
                            )
                            for candidate in current.candidates
                        )
                    }
                )
            run = current.run.model_copy(
                update={
                    "status": "queued" if can_retry else ("succeeded" if partial else "failed"),
                    "stage": "complete" if partial else current.run.stage,
                    "error": code,
                    "candidate_outcomes": tuple(candidate.outcome for candidate in current.candidates),
                    "completed_at": None if can_retry else now,
                }
            )
            await self.repository.store(connection, current.model_copy(update={"run": run}))
            await self._complete(connection, invocation)


def validate_bundle(
    bundle: GeneratedTraceLearningBundle,
    traces: tuple[CompleteTrace, ...],
    host: TraceLearningHostProfile,
    previous_runs: tuple[LearningRecord, ...] = (),
    resolved_tool_calls: tuple[ResolvedToolTraceCall, ...] = (),
) -> ValidationReport:
    """Prove only recorded query instantiations; never replay stale results for a changed SQL AST."""
    tool_keys = {item.key for item in bundle.tools}
    for skill in bundle.skills:
        if (
            skill.content.tool_dependencies
            or skill.content.package is not None
            or not set(skill.tool_keys) <= tool_keys
        ):
            raise TraceLearningError("invalid_tool_dependency")
        try:
            build_instruction_skill_package(skill.content)
        except SkillPackageError as error:
            raise TraceLearningError("invalid_skill_package") from error
    current_calls = {(trace.trace_id, call.call_id): call for trace in traces for call in trace.tool_calls}
    checked = 0
    mismatches: list[SQLMismatchFeedback] = []
    for tool in bundle.tools:
        implementation = tool.content.implementation
        if implementation.dialect != host.dialect or implementation.database_name != host.database_name:
            raise TraceLearningError("host_profile_mismatch")
        examples = [(example, current_calls, resolved_tool_calls) for example in tool.examples]
        # Preserve covered behaviors when a subsequent user import revises a known Tool.
        examples.extend(_previous_examples(previous_runs, host, tool.key))
        for example, calls, resolved in examples:
            try:
                checked += _validate_example(tool.key, tool.content, example, calls, host.dialect, resolved)
            except SQLMismatchError as error:
                # Keep diagnostics bounded while still checking other, non-repairable failures.
                mismatches.extend(error.feedback[: max(0, 32 - len(mismatches))])
    if mismatches:
        raise SQLMismatchError(tuple(mismatches))
    return ValidationReport(covered_path_verified=True, checked_examples=checked)


def _validate_example(
    tool_key: str,
    content,
    example: ToolTraceExample,
    calls: dict[tuple[str, str], Any],
    dialect: str,
    resolved_tool_calls: tuple[ResolvedToolTraceCall, ...],
) -> int:
    call = calls.get((example.trace_id, example.call_id))
    if call is None or not call.succeeded:
        raise TraceLearningError("invalid_trace_example")
    sql = _recorded_call_sql(call, example, resolved_tool_calls)
    try:
        rendered = render_tool_sql(content, example.arguments)
    except ValueError as error:
        raise TraceLearningError("invalid_tool_example_arguments") from error
    original = sqlglot.parse(sql, read=dialect)
    generated = sqlglot.parse(rendered, read=dialect)
    expected_sql = "; ".join(
        node.sql(dialect=dialect, normalize=True, comments=False) for node in original if node is not None
    )
    actual_sql = "; ".join(
        node.sql(dialect=dialect, normalize=True, comments=False) for node in generated if node is not None
    )
    if (
        len(original) != 1
        or len(generated) != 1
        or original[0] is None
        or generated[0] is None
        or expected_sql != actual_sql
    ):
        raise SQLMismatchError((
            SQLMismatchFeedback(
                tool_key=tool_key,
                trace_id=example.trace_id,
                call_id=example.call_id,
                query_index=example.query_index,
                expected_sql=expected_sql,
                actual_sql=actual_sql,
            ),
        ))
    return 1


def _recorded_call_sql(call, example: ToolTraceExample, resolved_tool_calls: tuple[ResolvedToolTraceCall, ...]) -> str:
    resolved = next(
        (item for item in resolved_tool_calls if (item.trace_id, item.call_id) == (example.trace_id, example.call_id)),
        None,
    )
    sql = resolved.executed_sql if resolved is not None else call.arguments.get("sql", call.arguments.get("query"))
    queries = call.arguments.get("queries")
    if resolved is not None:
        if example.query_index != 0:
            raise TraceLearningError("invalid_trace_example")
        queries = None
    elif _learned_receipt(call.result) is not None:
        raise TraceLearningError("trace_sql_unavailable")
    elif isinstance(queries, list):
        if example.query_index >= len(queries):
            raise TraceLearningError("invalid_trace_example")
        sql = queries[example.query_index]
    elif example.query_index != 0:
        raise TraceLearningError("invalid_trace_example")
    if not isinstance(sql, str):
        raise TraceLearningError("trace_sql_unavailable")
    _validate_observed_result(call.result, queries, example.query_index, sql)
    return sql


def _validate_observed_result(result, queries, query_index: int, sql: str) -> None:
    if isinstance(result, dict):
        _require_successful_result(result)
        artifact = result.get("artifact", result)
        outputs = artifact.get("queries") if isinstance(artifact, dict) else None
        if isinstance(outputs, list):
            if query_index >= len(outputs) or not isinstance(outputs[query_index], dict):
                raise TraceLearningError("invalid_trace_example")
            observed = outputs[query_index]
            _require_successful_result(observed)
            if observed.get("sql") is not None and observed["sql"] != sql:
                raise TraceLearningError("invalid_trace_example")
        elif isinstance(queries, list) and len(queries) > 1:
            raise TraceLearningError("trace_query_result_unavailable")


def _previous_examples(previous_runs: tuple[LearningRecord, ...], host: TraceLearningHostProfile, key: str):
    for previous in previous_runs:
        if previous.run.host_profile != host or previous.generated is None:
            continue
        calls = {(trace.trace_id, call.call_id): call for trace in previous.request.traces for call in trace.tool_calls}
        resolved = () if previous.generation_input is None else previous.generation_input.resolved_tool_calls
        for old_tool in previous.generated.tools:
            if old_tool.key == key:
                for example in old_tool.examples:
                    yield example, calls, resolved


def _learned_receipt(result) -> dict[str, Any] | None:
    if not isinstance(result, dict) or not isinstance(result.get("artifact"), dict):
        return None
    artifact = result["artifact"]
    if "powercontext_tool" not in artifact:
        return None
    receipt = artifact["powercontext_tool"]
    if not isinstance(receipt, dict):
        raise TraceLearningError("invalid_learned_tool_receipt")
    return receipt


def _validate_learned_receipt(call, receipt: dict[str, Any], tool: Tool, host: TraceLearningHostProfile) -> None:
    implementation = tool.content.implementation
    if (
        not call.succeeded
        or call.name != tool.content.name
        or receipt.get("name") != tool.content.name
        or receipt.get("sql") != implementation.sql
        or "database_name" not in receipt
        or (receipt["database_name"] or None) != implementation.database_name
        or receipt.get("arguments") != call.arguments
        or receipt.get("complete") is not True
        or implementation.dialect != host.dialect
        or implementation.database_name != host.database_name
    ):
        raise TraceLearningError("invalid_learned_tool_receipt")
    result = receipt.get("result")
    if (
        not isinstance(result, dict)
        or result.get("truncated") is not False
        or not isinstance(result.get("columns"), list)
        or not isinstance(result.get("rows"), list)
        or call.result.get("tool_call_id") != call.call_id
    ):
        raise TraceLearningError("invalid_learned_tool_receipt")
    try:
        _require_successful_result(call.result)
        _require_successful_result(result)
        if json.loads(call.result.get("content", "")) != result:
            raise TraceLearningError("invalid_learned_tool_receipt")
    except (TypeError, ValueError) as error:
        raise TraceLearningError("invalid_learned_tool_receipt") from error


def _require_successful_result(result: dict[str, Any]) -> None:
    if (
        result.get("error")
        or result.get("truncated") is True
        or result.get("success") is False
        or result.get("status") in ("error", "failed", "failure")
    ):
        raise TraceLearningError("invalid_trace_example")


def _sum_usage(previous: int | None, current: int | None) -> int | None:
    return None if previous is None and current is None else (previous or 0) + (current or 0)


def _ref_key(ref: ArtifactRef) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision


def _unique_refs(values):
    return tuple({value.model_dump_json(): value for value in values}.values())
