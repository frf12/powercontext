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
from typing import TYPE_CHECKING, TypedDict, TypeVar

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
from powercontext.builtin.trace_learning.consolidation import CandidateConsolidator, redirect_tool_dependencies
from powercontext.builtin.trace_learning.generation import (
    CandidateDiscoveryInput,
    CandidateGenerationInput,
    tool_review_input,
)
from powercontext.builtin.trace_learning.models import (
    CandidateFeedback,
    CandidateInventory,
    CandidateOutcome,
    CandidateResponse,
    CandidateStageFailure,
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
    from powercontext.builtin.artifacts.tool import ToolContent
    from powercontext.builtin.runtime.processing_execution import ScopeInvocation
    from powercontext.builtin.trace_learning.generation import CandidateLearningGenerator
    from powercontext.builtin.trace_learning.models import ReviewDecision, ToolReview
    from powercontext.builtin.trace_learning.service import TraceLearningService

T = TypeVar("T")
_TERMINAL = {"published", "rejected", "deferred"}


class _ReviewHistory(TypedDict, total=False):
    previous_tool: ToolContent
    previous_review: ToolReview
    decisions: tuple[ReviewDecision, ...]


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
        self.consolidator = CandidateConsolidator(self)

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
        candidate = self.record.candidates[index]
        outcome = candidate.outcome.model_copy(update={"status": status, "reason": reason, **changes})
        checkpoint = {}
        if status in {"deferred", "rejected"} and candidate.outcome.status not in _TERMINAL:
            checkpoint["resume_status"] = candidate.outcome.status
        return await self._update(index, outcome=outcome, **checkpoint)

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
        for candidate in self.record.candidates:
            for previous in (*(candidate.history or ()), *(candidate.consolidation_history or ())):
                await self.service._authorize(scope, principal, "read", previous.ref)

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
        indices = sorted(
            range(len(self.record.candidates)),
            key=lambda i: {"tool": 0, "experience": 1, "skill": 2}[self.record.candidates[i].spec.family],
        )
        # Give every executable capability a first draft before one repair conversation
        # consumes the Run. Preserve inventory indices for durable checkpoints.
        for index in indices:
            candidate = self.record.candidates[index]
            if (
                candidate.spec.family == "tool"
                and not candidate.revisions
                and candidate.outcome.status not in _TERMINAL
            ):
                await self._attempt(index, generate_only=True)
        while True:
            await self._unblock_skills()
            pending = [i for i in indices if self.record.candidates[i].outcome.status not in _TERMINAL]
            if not pending:
                break
            for index in pending:
                await self._attempt(index)
        await self._finish()

    async def _attempt(self, index: int, *, generate_only: bool = False) -> None:
        try:
            if generate_only:
                await self._generate(index)
            else:
                await self._process(index)
        except (InvalidInferenceOutputError, ValidationError):
            await self._status(index, "deferred", "invalid_generation_output")
        except RevisionConflictError:
            await self._status(index, "deferred", "artifact_revision_conflict")
        except SkillPackageError:
            await self._status(index, "rejected", "invalid_skill_package")
        except TraceLearningError as error:
            await self._status(
                index,
                "deferred"
                if error.code.endswith("budget_exceeded")
                or error.code in {"consolidation_disputed", "unavailable_tool_dependency"}
                else "rejected",
                error.code,
            )

    async def _unblock_skills(self) -> None:
        available = {item.key for item in self._tools()}
        for index, candidate in enumerate(self.record.candidates):
            if (
                candidate.spec.family == "skill"
                and candidate.outcome.reason == "unavailable_tool_dependency"
                and set(candidate.spec.tool_keys) <= available
            ):
                await self._status(
                    index, candidate.resume_status or ("reviewing" if candidate.revisions else "planned")
                )

    async def _discover(self) -> None:
        if self.record.generation_input is None:
            raise TraceLearningError("invalid_checkpoint")
        if self.record.discovered_inventory is None:
            await self._discover_inventory()
        inventory = self._inventory()
        if self.record.inventory_review_error is None and any(spec.family == "skill" for spec in inventory.candidates):
            inventory = await self._review_inventory(inventory)
        identity_errors = self._skill_reuse_errors(inventory)
        counts: Counter[str] = Counter()
        trace_ids = {trace.trace_id for trace in self.record.request.traces}
        candidates = []
        for spec in sorted(
            inventory.candidates, key=lambda item: {"experience": 0, "tool": 1, "skill": 2}[item.family]
        ):
            counts[spec.family] += 1
            outcome = CandidateOutcome(family=spec.family, key=spec.key)
            if not set(spec.trace_ids) <= trace_ids:
                outcome = outcome.model_copy(update={"status": "rejected", "reason": "unknown_trace_id"})
            elif spec.family == "skill" and self.record.inventory_review_error is not None:
                outcome = outcome.model_copy(
                    update={"status": "deferred", "reason": self.record.inventory_review_error}
                )
            elif (spec.family, spec.key) in identity_errors:
                outcome = outcome.model_copy(update={"status": "rejected", "reason": "invalid_skill_reuse"})
            elif counts[spec.family] > self.record.run.budget.max_candidates_per_family:
                outcome = outcome.model_copy(update={"status": "deferred", "reason": "candidate_limit"})
            candidates.append(LearningCandidate(spec=spec, outcome=outcome))
        self.record = self.record.model_copy(
            update={
                "candidate_plan_ready": True,
                "candidates": tuple(candidates),
                "generated": GeneratedTraceLearningBundle(),
            }
        )
        await self._store()

    def _inventory(self) -> CandidateInventory:
        if self.record.discovered_inventory is None:
            raise TraceLearningError("invalid_checkpoint")
        return self.record.discovered_inventory

    async def _review_inventory(self, inventory: CandidateInventory) -> CandidateInventory:
        # Review before fixing keys, while methods and dependencies can still be separated.
        # Checkpoints prevent replaying completed discovery work after a worker restart.
        while self.record.inventory_review_rounds <= self.record.run.budget.max_candidate_repair_rounds:
            errors = self._skill_reuse_errors(inventory)
            if self.record.inventory_review_rounds and not errors:
                break
            try:
                await self._discover_inventory(inventory, tuple(errors.values()))
            except (InvalidInferenceOutputError, ValidationError):
                await self._defer_inventory_skills("invalid_generation_output")
                break
            except TraceLearningError as error:
                if not error.code.endswith("budget_exceeded"):
                    raise
                await self._defer_inventory_skills(error.code)
                break
            inventory = self._inventory()
        return inventory

    async def _defer_inventory_skills(self, reason: str) -> None:
        self.record = self.record.model_copy(update={"inventory_review_error": reason})
        await self._store()

    def _skill_reuse_errors(self, inventory: CandidateInventory) -> dict[tuple[str, str], str]:
        context = self.record.generation_input
        if context is None:
            raise TraceLearningError("invalid_checkpoint")
        previous = {item.key: item for item in context.previous_artifacts if item.ref.family == "skill"}
        errors = {}
        for spec in inventory.candidates:
            if spec.family != "skill":
                continue
            old, decision = previous.get(spec.key), spec.skill_reuse
            if old is not None and (decision is None or decision.ref != old.ref):
                errors[(spec.family, spec.key)] = (
                    f"Skill {spec.key!r}: reusing this key requires skill_reuse with exact ref "
                    f"{old.ref.model_dump_json()} and a same_method_reason comparing goal, inputs/outputs and "
                    "procedure. If this is a different method, choose a distinct key instead."
                )
            elif old is None and decision is not None:
                errors[(spec.family, spec.key)] = (
                    f"Skill {spec.key!r}: no previous Skill with this key is visible; a new method uses "
                    "skill_reuse=null. Only reuse an exact Skill ref from the visible catalog."
                )
        return errors

    async def _discover_inventory(self, inventory: CandidateInventory | None = None, feedback: tuple[str, ...] = ()):
        value = CandidateDiscoveryInput(
            context=self.record.generation_input if inventory is None else None,
            max_candidates_per_family=self.record.run.budget.max_candidates_per_family,
            inventory=inventory,
            feedback=feedback,
        )
        self._check_input(value, self.record.discovery_messages)
        try:
            result = await self._call(
                lambda limit: self.generator.discover(
                    value, messages=self.record.discovery_messages, max_requests=limit
                ),
                stage="discovering",
            )
        except InferenceError as error:
            self.record = self.record.model_copy(
                update={"discovery_messages": error.messages or self.record.discovery_messages}
            )
            await self._store()
            raise
        self.record = self.record.model_copy(
            update={
                "discovered_inventory": result.output,
                "discovery_messages": result.messages,
                "inventory_review_rounds": self.record.inventory_review_rounds + int(inventory is not None),
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
            if self.consolidator.enabled and not candidate.consolidation_applied and candidate.spec.family == "tool":
                errors = await self._validate(index, preliminary=True)
                if errors:
                    await self._repair(index, CandidateFeedback(validation_errors=errors))
                    return
            if await self.consolidator.reconcile(index):
                return
            candidate = self.record.candidates[index]
            errors = await self._validate(index)
            if errors:
                await self._repair(
                    index,
                    CandidateFeedback(
                        validation_errors=errors, review=candidate.feedback.review if candidate.feedback else None
                    ),
                )
                return
            item = candidate.revisions[-1].candidate
            if isinstance(item, GeneratedTool) and not candidate.review_resolved:
                if candidate.reviewed_revision == len(candidate.revisions):
                    await self._repair(index, CandidateFeedback(review=candidate.reviews[-1]))
                    return
                if self._generator_closed_review(candidate):
                    await self._update(index, review_resolved=True, review_resolution="generator_self_pass")
                else:
                    await self._review(index, item)
                    candidate = self.record.candidates[index]
                    review = candidate.reviews[-1]
                    if review.findings:
                        await self._repair(index, CandidateFeedback(review=review))
                        return
            await self._status(index, "ready")

    @staticmethod
    def _generator_closed_review(candidate: LearningCandidate) -> bool:
        if len(candidate.revisions) < 2 or candidate.feedback is None or candidate.feedback.review is None:
            return False
        last, previous = candidate.revisions[-1], candidate.revisions[-2]
        return last.self_pass or (
            last.candidate == previous.candidate and all(item.decision == "reject" for item in last.decisions)
        )

    async def _generate(self, index: int) -> None:
        await self.consolidator.prepare(index)
        candidate = self.record.candidates[index]
        context = self.record.generation_input
        if context is None:
            raise TraceLearningError("invalid_checkpoint")
        tool_sources = ()
        if (
            candidate.spec.family == "tool"
            and self.generator.parameter_plans
            and (candidate.generation_format == "parameter_plan" or not candidate.messages)
        ):
            from powercontext.builtin.trace_learning.parameter_plan import recorded_tool_sources

            tool_sources = recorded_tool_sources(context, candidate.spec.trace_ids, candidate.spec.source_calls)
            if not tool_sources:
                raise TraceLearningError("trace_sql_unavailable")
            candidate = await self._update(index, generation_format="parameter_plan")
        if candidate.messages:
            context = None
        else:
            context = context.model_copy(
                update={
                    "traces": tuple(trace for trace in context.traces if trace.trace_id in candidate.spec.trace_ids),
                    "previous_artifacts": context.previous_artifacts
                    if candidate.history is None
                    else candidate.history,
                }
            )
        value = CandidateGenerationInput(
            candidate=candidate.spec,
            context=context,
            available_tools=self._tools(),
            feedback=candidate.feedback,
            tool_sources=tool_sources,
            host_profile=self.record.run.host_profile if tool_sources else None,
        )
        self._check_input(value, candidate.messages)
        await self._status(index, "generating")
        try:
            result = await self._call(
                lambda limit: self.generator.generate_candidate(value, messages=candidate.messages, max_requests=limit),
                stage="generating",
            )
        except InferenceError as error:
            await self._update(
                index,
                messages=error.messages or candidate.messages,
                stage_failures=(
                    *candidate.stage_failures,
                    CandidateStageFailure(
                        stage="generating",
                        code=type(error).__name__,
                        revision=len(candidate.revisions),
                        model_calls=self.record.run.usage.model_calls,
                    ),
                ),
            )
            raise
        response = CandidateResponse[GeneratedCandidate].model_validate(result.output.model_dump())
        await self._update(
            index,
            messages=result.messages,
            revisions=(*candidate.revisions, response),
            review_resolved=False,
            review_resolution=None,
            outcome=candidate.outcome.model_copy(update={"status": "reviewing", "reason": None}),
        )

    async def _validate(self, index: int, *, preliminary: bool = False) -> tuple[str, ...]:  # noqa: C901 - identity, contract and recorded-path validation
        from powercontext.builtin.trace_learning.service import validate_bundle

        candidate = self.record.candidates[index]
        response = candidate.revisions[-1]
        if response.consolidation_conflict is not None:
            raise TraceLearningError("consolidation_disputed")
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
        if (
            not preliminary
            and isinstance(item, GeneratedTool)
            and any(old.content.name == item.content.name and old.key != item.key for old in self._tools())
        ):
            return ("Another tool has this callable name; give distinct capabilities distinct callable names.",)
        if isinstance(item, GeneratedSkill) and set(item.tool_keys) != set(candidate.spec.tool_keys):
            return ("Skill tool_keys must match its planned dependencies.",)
        bundle = _bundle(item, self._tools() if isinstance(item, GeneratedSkill) else ())
        async with self.service._transaction(self.invocation) as connection:
            previous = await self.service.repository.list_completed(
                connection, self.record.run.scope_id, limit=None, include_partial=self.consolidator.enabled
            )
        if self.consolidator.enabled:
            previous = (*tuple(old for old in previous if old.run.run_id != self.record.run.run_id), self.record)
        decision = candidate.consolidation
        if isinstance(item, GeneratedTool) and decision is not None and decision.target is not None:
            target = next(old for old in candidate.consolidation_history or () if old.ref == decision.target)
            old_content = target.content
            if item.content.name != old_content.get("name"):
                return ("Preserve the historical Tool's callable name when revising its identity.",)
            for field in ("input_schema", "output_schema"):
                if _schema_contract(getattr(item.content, field)) != _schema_contract(old_content.get(field)):
                    return (
                        f"Preserve the historical Tool's {field} validation contract; documentation may be clarified. "
                        "Do not narrow accepted values or change parameter/output meanings when reusing an identity.",
                    )
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
        await self._update(index, validation=validation)
        return ()

    async def _review(self, index: int, item: GeneratedTool) -> None:
        candidate = self.record.candidates[index]
        followup: _ReviewHistory = {}
        if candidate.reviewed_revision and candidate.reviews:
            previous = candidate.revisions[candidate.reviewed_revision - 1].candidate
            if isinstance(previous, GeneratedTool):
                followup = {
                    "previous_tool": previous.content,
                    "previous_review": candidate.reviews[-1],
                    "decisions": candidate.revisions[-1].decisions,
                }
        self._check_input(tool_review_input(item.content, **followup))
        for attempt in range(self.record.run.budget.max_candidate_review_retries + 1):
            candidate = self.record.candidates[index]
            try:
                result = await self._call(
                    lambda limit: self.generator.review_tool(item.content, max_requests=limit, **followup),
                    stage="reviewing",
                )
                break
            except InferenceError as error:
                await self._update(
                    index,
                    review_messages=(*candidate.review_messages, error.messages),
                    stage_failures=(
                        *candidate.stage_failures,
                        CandidateStageFailure(
                            stage="reviewing",
                            code=type(error).__name__,
                            revision=len(candidate.revisions),
                            model_calls=self.record.run.usage.model_calls,
                        ),
                    ),
                )
                if not isinstance(error, InvalidInferenceOutputError) or (
                    attempt == self.record.run.budget.max_candidate_review_retries
                ):
                    raise
        else:
            raise TraceLearningError("invalid_checkpoint")
        candidate = self.record.candidates[index]
        await self._update(
            index,
            reviews=(*candidate.reviews, result.output),
            review_messages=(*candidate.review_messages, result.messages),
            reviewed_revision=len(candidate.revisions),
            review_resolved=not result.output.findings,
            review_resolution=None if result.output.findings else "reviewer_pass",
            feedback=CandidateFeedback(
                review=result.output,
                consolidation=None if candidate.feedback is None else candidate.feedback.consolidation,
            ),
            outcome=candidate.outcome.model_copy(update={"review_rounds": candidate.outcome.review_rounds + 1}),
        )

    async def _repair(self, index: int, feedback: CandidateFeedback) -> bool:
        candidate = self.record.candidates[index]
        if candidate.feedback is not None and candidate.feedback.consolidation is not None:
            feedback = feedback.model_copy(update={"consolidation": candidate.feedback.consolidation})
        await self._update(index, feedback=feedback)
        validation = bool(feedback.validation_errors)
        budget = self.record.run.budget
        limit = (
            (
                candidate.validation_repair_limit
                if candidate.validation_repair_limit is not None
                else budget.max_candidate_validation_repairs
            )
            if validation
            else (candidate.repair_limit if candidate.repair_limit is not None else budget.max_candidate_repair_rounds)
        )
        rounds = (
            candidate.validation_repair_rounds
            if validation
            else candidate.outcome.repair_rounds - candidate.validation_repair_rounds
        )
        if rounds >= limit:
            await self._status(
                index,
                "rejected" if feedback.validation_errors else "deferred",
                "validation_failed" if feedback.validation_errors else "review_unresolved",
            )
            return False
        if validation:
            await self._update(index, validation_repair_rounds=candidate.validation_repair_rounds + 1)
        await self._status(index, "repairing", repair_rounds=candidate.outcome.repair_rounds + 1)
        return True

    async def _publish(self, index: int) -> None:
        candidate = self.record.candidates[index]
        item = candidate.revisions[-1].candidate
        existing = self.record.generated or GeneratedTraceLearningBundle()
        context = self.record.generation_input
        publication = self.record.model_copy(
            update={
                "generated": _bundle(item),
                "generation_input": context
                if context is None or candidate.history is None
                else context.model_copy(update={"previous_artifacts": candidate.history}),
            }
        )
        await self._authorize_evidence()
        vectors = await self.service._prepare_bundle_vectors(publication)
        async with self.service._transaction(self.invocation) as connection:
            await self._authorize_evidence()
            saved = await self.service._save_bundle(connection, publication, vectors)
            group = {"experience": "experiences", "tool": "tools", "skill": "skills"}[candidate.spec.family]
            aggregate = existing.model_copy(
                update={group: (*(old for old in getattr(existing, group) if old.key != item.key), item)}
            )
            candidates = list(saved.candidates)
            redirect_tool_dependencies(candidates, candidate)
            candidates[index] = candidate.model_copy(
                update={"outcome": candidate.outcome.model_copy(update={"status": "published", "reason": None})}
            )
            self.record = saved.model_copy(
                update={"generated": aggregate, "candidates": tuple(candidates), "generation_input": context}
            )
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


def _schema_contract(value):
    """Ignore documentation while preserving validation keywords and parameter names."""
    if isinstance(value, list):
        return [_schema_contract(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"description", "title", "examples", "$comment"}:
            continue
        if key in {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"} and isinstance(
            item, dict
        ):
            result[key] = {name: _schema_contract(schema) for name, schema in item.items()}
        elif key in {
            "allOf",
            "anyOf",
            "oneOf",
            "not",
            "if",
            "then",
            "else",
            "items",
            "prefixItems",
            "contains",
            "additionalProperties",
            "unevaluatedProperties",
            "propertyNames",
            "additionalItems",
            "unevaluatedItems",
        }:
            result[key] = _schema_contract(item)
        else:
            result[key] = item
    return result
