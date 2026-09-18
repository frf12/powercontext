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

"""Durable per-candidate generation and independent review under the existing Supervisor."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from pydantic import ValidationError

from powercontext.builtin.artifacts.skill import SkillPackageError
from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.inference.usage import report_generation_usage
from powercontext.builtin.persistence.dream import database_now
from powercontext.builtin.trace_learning.generation import (
    CandidateDiscoveryInput,
    CandidateGenerationInput,
    ToolReviewInput,
)
from powercontext.builtin.trace_learning.models import (
    CandidateFeedback,
    CandidateOutcome,
    CandidateResponse,
    GeneratedCandidate,
    GeneratedExperience,
    GeneratedSkill,
    GeneratedTool,
    GeneratedTraceLearningBundle,
    LearningCandidate,
    LearningRecord,
    SQLMismatchError,
    TraceLearningError,
    ValidationReport,
)
from powercontext.errors import RevisionConflictError

if TYPE_CHECKING:
    from powercontext.builtin.runtime.processing_execution import ScopeInvocation
    from powercontext.builtin.trace_learning.generation import CandidateLearningGenerator
    from powercontext.builtin.trace_learning.service import TraceLearningService

T = TypeVar("T")
_TERMINAL = {"published", "rejected", "deferred"}


class CandidateWorkflow:
    def __init__(
        self,
        service: TraceLearningService,
        invocation: ScopeInvocation,
        record: LearningRecord,
        generator: CandidateLearningGenerator,
    ) -> None:
        self.service = service
        self.invocation = invocation
        self.record = record
        self.generator = generator

    async def _store(self) -> None:
        self.record = self.record.model_copy(
            update={
                "run": self.record.run.model_copy(
                    update={
                        "candidate_outcomes": tuple(item.outcome for item in self.record.candidates),
                    }
                )
            }
        )
        async with self.service._transaction(self.invocation) as connection:
            await self.service.repository.store(connection, self.record)

    async def _update(self, index: int, **changes) -> LearningCandidate:
        candidates = list(self.record.candidates)
        candidates[index] = candidates[index].model_copy(update=changes)
        self.record = self.record.model_copy(update={"candidates": tuple(candidates)})
        await self._store()
        return candidates[index]

    async def _status(self, index: int, status: str, reason: str | None = None, **changes) -> LearningCandidate:
        outcome = self.record.candidates[index].outcome.model_copy(
            update={"status": status, "reason": reason, **changes}
        )
        return await self._update(index, outcome=outcome)

    async def _authorize_evidence(self) -> None:
        scope, principal = self.record.run.scope_id, self.record.principal_id
        await self.service._authorize(scope, principal, "contribute")
        for ref in self.record.run.sources:
            await self.service._authorize(scope, principal, "read", ref)
        context = self.record.generation_input
        if context is not None:
            refs = [item.ref for item in context.previous_artifacts]
            refs.extend(item.tool_ref for item in context.resolved_tool_calls)
            for ref in refs:
                await self.service._authorize(scope, principal, "read", ref)

    async def _call(
        self, dispatch: Callable[[int], Awaitable[GenerationResult[T]]], *, stage: str
    ) -> GenerationResult[T]:
        run = self.record.run
        limit = min(self.generator.max_requests, run.budget.max_model_calls - run.usage.model_calls)
        if limit < 1:
            raise TraceLearningError("budget_exceeded")
        await self._authorize_evidence()
        # Reserve before dispatch. A worker lost mid-request conservatively retains its reservation.
        self.record = self.record.model_copy(
            update={
                "run": run.model_copy(
                    update={
                        "stage": stage,
                        "usage": run.usage.model_copy(
                            update={
                                "model_calls": run.usage.model_calls + limit,
                                "reserved_model_calls": run.usage.reserved_model_calls + limit,
                            }
                        ),
                    }
                )
            }
        )
        await self._store()
        usage: InferenceUsage | None = None
        try:
            result = await dispatch(limit)
            usage = result.usage
            responses = [message for message in result.messages if message.get("kind") == "response"]
            response_usage = (
                [message.get("usage") for message in responses[-usage.requests :]] if usage.requests else []
            )
            exceeds = any(
                isinstance(item, dict)
                and isinstance(item.get("output_tokens"), int)
                and item["output_tokens"] > run.budget.max_output_tokens
                for item in response_usage
            )
            if exceeds or (
                not response_usage
                and usage.output_tokens is not None
                and usage.output_tokens > run.budget.max_output_tokens * max(1, usage.requests)
            ):
                error = InvalidInferenceOutputError("generate", "learning output token budget exceeded")
                error.usage, error.messages = usage, result.messages
                raise error
        except InferenceError as error:
            usage = error.usage
            if usage is None and isinstance(error, InferenceConfigurationError):
                usage = InferenceUsage(requests=0)
            raise
        finally:
            self.record = self.record.model_copy(
                update={
                    "run": self.record.run.model_copy(
                        update={
                            "usage": self.record.run.usage.model_copy(
                                update={
                                    "model_calls": run.usage.model_calls + (limit if usage is None else usage.requests),
                                    "reserved_model_calls": run.usage.reserved_model_calls
                                    + (limit if usage is None else 0),
                                    "input_tokens": _sum(
                                        run.usage.input_tokens, None if usage is None else usage.input_tokens
                                    ),
                                    "output_tokens": _sum(
                                        run.usage.output_tokens, None if usage is None else usage.output_tokens
                                    ),
                                }
                            ),
                        }
                    )
                }
            )
            await self._store()
            if usage is not None:
                await report_generation_usage(usage)
        return result

    def _check_input(self, value, messages=()) -> None:
        if (
            len(value.model_dump_json()) + len(json.dumps(messages, ensure_ascii=False))
            > self.record.run.budget.max_input_chars
        ):
            raise TraceLearningError("input_budget_exceeded")

    async def execute(self) -> None:
        if not self.record.candidate_plan_ready:
            await self._discover()
        for index in range(len(self.record.candidates)):
            if self.record.candidates[index].outcome.status in _TERMINAL:
                continue
            try:
                await self._process(index)
            except (InvalidInferenceOutputError, ValidationError):
                await self._status(index, "deferred", "invalid_generation_output")
            except RevisionConflictError:
                await self._status(index, "deferred", "artifact_revision_conflict")
            except SkillPackageError:
                await self._status(index, "rejected", "invalid_skill_package")
            except TraceLearningError as error:
                await self._status(
                    index, "deferred" if error.code.endswith("budget_exceeded") else "rejected", error.code
                )
        await self._finish()

    async def _discover(self) -> None:
        if self.record.generation_input is None:
            raise TraceLearningError("invalid_checkpoint")
        value = CandidateDiscoveryInput(
            context=self.record.generation_input,
            max_candidates_per_family=self.record.run.budget.max_candidates_per_family,
        )
        self._check_input(value, self.record.discovery_messages)
        try:
            result = await self._call(
                lambda limit: self.generator.discover(
                    value,
                    messages=self.record.discovery_messages,
                    max_requests=limit,
                ),
                stage="discovering",
            )
        except InferenceError as error:
            self.record = self.record.model_copy(update={"discovery_messages": error.messages})
            await self._store()
            raise
        counts: Counter[str] = Counter()
        trace_ids = {trace.trace_id for trace in self.record.request.traces}
        candidates = []
        for spec in sorted(
            result.output.candidates, key=lambda item: {"experience": 0, "tool": 1, "skill": 2}[item.family]
        ):
            counts[spec.family] += 1
            outcome = CandidateOutcome(family=spec.family, key=spec.key)
            if not set(spec.trace_ids) <= trace_ids:
                outcome = outcome.model_copy(update={"status": "rejected", "reason": "unknown_trace_id"})
            elif counts[spec.family] > self.record.run.budget.max_candidates_per_family:
                outcome = outcome.model_copy(update={"status": "deferred", "reason": "candidate_limit"})
            candidates.append(LearningCandidate(spec=spec, outcome=outcome))
        self.record = self.record.model_copy(
            update={
                "candidate_plan_ready": True,
                "discovery_messages": result.messages,
                "candidates": tuple(candidates),
                "generated": GeneratedTraceLearningBundle(),
            }
        )
        await self._store()

    def _tools(self) -> tuple[GeneratedTool, ...]:
        return () if self.record.generated is None else self.record.generated.tools

    async def _process(self, index: int) -> None:  # noqa: C901 - checkpointed generation, validation, review and publication
        while True:
            candidate = self.record.candidates[index]
            if candidate.outcome.status == "ready":
                await self._publish(index)
                return
            if candidate.spec.family == "skill" and not set(candidate.spec.tool_keys) <= {
                item.key for item in self._tools()
            }:
                raise TraceLearningError("unavailable_tool_dependency")
            if not candidate.revisions or candidate.outcome.status in {"planned", "generating", "repairing"}:
                await self._generate(index)
                candidate = self.record.candidates[index]
            errors = await self._validate(candidate)
            if errors:
                if not await self._repair(
                    index,
                    CandidateFeedback(
                        validation_errors=errors, review=candidate.feedback.review if candidate.feedback else None
                    ),
                ):
                    return
                continue
            item = candidate.revisions[-1].candidate
            if isinstance(item, GeneratedTool) and not candidate.review_resolved:
                if candidate.reviewed_revision == len(candidate.revisions):
                    if not await self._repair(index, CandidateFeedback(review=candidate.reviews[-1])):
                        return
                    continue
                if self._declined_unchanged_review(candidate):
                    await self._update(index, review_resolved=True)
                else:
                    await self._review(index, item)
                    candidate = self.record.candidates[index]
                    review = candidate.reviews[-1]
                    if review.findings:
                        if not await self._repair(index, CandidateFeedback(review=review)):
                            return
                        continue
            await self._status(index, "ready")

    @staticmethod
    def _declined_unchanged_review(candidate: LearningCandidate) -> bool:
        if len(candidate.revisions) < 2 or candidate.feedback is None or candidate.feedback.review is None:
            return False
        last, previous = candidate.revisions[-1], candidate.revisions[-2]
        return last.candidate == previous.candidate and all(item.decision == "reject" for item in last.decisions)

    async def _generate(self, index: int) -> None:
        candidate = self.record.candidates[index]
        context = self.record.generation_input
        if context is None:
            raise TraceLearningError("invalid_checkpoint")
        if candidate.messages:
            context = None
        else:
            context = context.model_copy(
                update={
                    "traces": tuple(trace for trace in context.traces if trace.trace_id in candidate.spec.trace_ids)
                }
            )
        value = CandidateGenerationInput(
            candidate=candidate.spec, context=context, available_tools=self._tools(), feedback=candidate.feedback
        )
        self._check_input(value, candidate.messages)
        await self._status(index, "generating")
        try:
            result = await self._call(
                lambda limit: self.generator.generate_candidate(value, messages=candidate.messages, max_requests=limit),
                stage="generating",
            )
        except InferenceError as error:
            await self._update(index, messages=error.messages or candidate.messages)
            raise
        response = CandidateResponse[GeneratedCandidate].model_validate(result.output.model_dump())
        await self._update(
            index,
            messages=result.messages,
            revisions=(*candidate.revisions, response),
            review_resolved=False,
            outcome=candidate.outcome.model_copy(update={"status": "reviewing", "reason": None}),
        )

    async def _validate(self, candidate: LearningCandidate) -> tuple[str, ...]:
        from powercontext.builtin.trace_learning.service import validate_bundle

        response = candidate.revisions[-1]
        item = response.candidate
        kind = {"experience": GeneratedExperience, "tool": GeneratedTool, "skill": GeneratedSkill}[
            candidate.spec.family
        ]
        if not isinstance(item, kind) or item.key != candidate.spec.key:
            return ("Keep the requested candidate family and key.",)
        if (
            candidate.feedback is not None
            and candidate.feedback.review is not None
            and candidate.reviewed_revision < len(candidate.revisions)
        ):
            expected = {finding.id for finding in candidate.feedback.review.findings}
            actual = [decision.finding_id for decision in response.decisions]
            if set(actual) != expected or len(actual) != len(expected):
                return ("Respond to every review finding exactly once with a decision and nonempty reason.",)
        if isinstance(item, GeneratedTool) and any(
            old.content.name == item.content.name and old.key != item.key for old in self._tools()
        ):
            return ("Another tool has this callable name; give distinct capabilities distinct callable names.",)
        if isinstance(item, GeneratedSkill) and set(item.tool_keys) != set(candidate.spec.tool_keys):
            return ("Skill tool_keys must match its planned dependencies.",)
        bundle = _bundle(item, self._tools() if isinstance(item, GeneratedSkill) else ())
        async with self.service._transaction(self.invocation) as connection:
            previous = await self.service.repository.list_completed(connection, self.record.run.scope_id, limit=None)
        try:
            validation = validate_bundle(
                bundle,
                self.record.request.traces,
                self.record.run.host_profile,
                previous,
                () if self.record.generation_input is None else self.record.generation_input.resolved_tool_calls,
            )
        except SQLMismatchError as error:
            return tuple(feedback.model_dump_json() for feedback in error.feedback)
        except (TraceLearningError, ValidationError, ValueError) as error:
            return (str(error)[:8000],)
        index = next(i for i, value in enumerate(self.record.candidates) if value.spec == candidate.spec)
        await self._update(index, validation=validation)
        return ()

    async def _review(self, index: int, item: GeneratedTool) -> None:
        candidate = self.record.candidates[index]
        self._check_input(ToolReviewInput(tool=item.content))
        try:
            result = await self._call(
                lambda limit: self.generator.review_tool(item.content, max_requests=limit), stage="reviewing"
            )
        except InferenceError as error:
            await self._update(index, review_messages=(*candidate.review_messages, error.messages))
            raise
        await self._update(
            index,
            reviews=(*candidate.reviews, result.output),
            review_messages=(*candidate.review_messages, result.messages),
            reviewed_revision=len(candidate.revisions),
            review_resolved=not result.output.findings,
            feedback=CandidateFeedback(review=result.output),
            outcome=candidate.outcome.model_copy(update={"review_rounds": candidate.outcome.review_rounds + 1}),
        )

    async def _repair(self, index: int, feedback: CandidateFeedback) -> bool:
        candidate = self.record.candidates[index]
        await self._update(index, feedback=feedback)
        if candidate.outcome.repair_rounds >= self.record.run.budget.max_candidate_repair_rounds:
            await self._status(
                index,
                "rejected" if feedback.validation_errors else "deferred",
                "validation_failed" if feedback.validation_errors else "review_unresolved",
            )
            return False
        await self._status(index, "repairing", repair_rounds=candidate.outcome.repair_rounds + 1)
        return True

    async def _publish(self, index: int) -> None:
        candidate = self.record.candidates[index]
        item = candidate.revisions[-1].candidate
        existing = self.record.generated or GeneratedTraceLearningBundle()
        async with self.service._transaction(self.invocation) as connection:
            await self._authorize_evidence()
            saved = await self.service._save_bundle(
                connection, self.record.model_copy(update={"generated": _bundle(item)})
            )
            group = {"experience": "experiences", "tool": "tools", "skill": "skills"}[candidate.spec.family]
            aggregate = existing.model_copy(update={group: (*getattr(existing, group), item)})
            candidates = list(saved.candidates)
            candidates[index] = candidate.model_copy(
                update={"outcome": candidate.outcome.model_copy(update={"status": "published", "reason": None})}
            )
            self.record = saved.model_copy(update={"generated": aggregate, "candidates": tuple(candidates)})
            self.record = self.record.model_copy(
                update={
                    "run": self.record.run.model_copy(
                        update={
                            "candidate_outcomes": tuple(value.outcome for value in candidates),
                        }
                    )
                }
            )
            await self.service.repository.store(connection, self.record)

    async def _finish(self) -> None:
        published = [item for item in self.record.candidates if item.outcome.status == "published"]
        tools = [item for item in published if item.spec.family == "tool"]
        validation = ValidationReport(
            covered_path_verified=bool(tools),
            checked_examples=sum(item.validation.checked_examples for item in tools if item.validation is not None),
        )
        async with self.service._transaction(self.invocation) as connection:
            self.record = self.record.model_copy(
                update={
                    "run": self.record.run.model_copy(
                        update={
                            "status": "succeeded" if published else "failed",
                            "stage": "complete",
                            "validation": validation,
                            "error": None if published else "no_valid_candidates",
                            "completed_at": await database_now(connection),
                        }
                    )
                }
            )
            await self.service.repository.store(connection, self.record)
            await self.service._complete(connection, self.invocation)


def _bundle(item: GeneratedCandidate, tools: tuple[GeneratedTool, ...] = ()) -> GeneratedTraceLearningBundle:
    if isinstance(item, GeneratedExperience):
        return GeneratedTraceLearningBundle(experiences=(item,))
    if isinstance(item, GeneratedTool):
        return GeneratedTraceLearningBundle(tools=(item,))
    return GeneratedTraceLearningBundle(
        skills=(item,), tools=tuple(tool for tool in tools if tool.key in item.tool_keys)
    )


def _sum(previous: int | None, current: int | None) -> int | None:
    return None if previous is None and current is None else (previous or 0) + (current or 0)
