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

"""Retrieve related capabilities and reconcile one candidate before publication."""

from __future__ import annotations

from typing import TYPE_CHECKING

from powercontext.builtin.artifacts.tool.search import tool_search_text
from powercontext.builtin.inference.errors import InferenceError
from powercontext.builtin.trace_learning.generation import CandidateConsolidationInput
from powercontext.builtin.trace_learning.models import (
    TRACE_LEARNING_PROMPT_VERSION,
    CandidateFeedback,
    ConsolidationFeedback,
    GeneratedTool,
    LearningCandidate,
    SkillReuseDecision,
    TraceLearningError,
)

if TYPE_CHECKING:
    from powercontext.builtin.trace_learning.workflow import CandidateWorkflow


class CandidateConsolidator:
    def __init__(self, workflow: CandidateWorkflow) -> None:
        self.workflow = workflow

    @property
    def enabled(self) -> bool:
        return self.workflow.record.run.prompt_version == TRACE_LEARNING_PROMPT_VERSION

    async def prepare(self, index: int) -> None:
        workflow = self.workflow
        candidate = workflow.record.candidates[index]
        if not self.enabled or candidate.history is not None:
            return
        questions = tuple(
            trace.question for trace in workflow.record.request.traces if trace.trace_id in candidate.spec.trace_ids
        )
        history = await workflow.service.related_previous(
            workflow.record, (candidate.spec.purpose, *questions), family=candidate.spec.family, key=candidate.spec.key
        )
        await workflow._update(index, history=history)

    async def reconcile(self, index: int) -> bool:
        """Return True when the original generating conversation must incorporate a target."""
        workflow = self.workflow
        candidate = workflow.record.candidates[index]
        if not self.enabled or candidate.consolidation_applied:
            return False
        item = candidate.revisions[-1].candidate
        if candidate.consolidation_history is None:
            content = (
                tool_search_text(item.content) if isinstance(item, GeneratedTool) else item.content.model_dump_json()
            )
            history = await workflow.service.related_previous(
                workflow.record, (content,), family=candidate.spec.family, key=candidate.spec.key
            )
            candidate = await workflow._update(index, consolidation_history=history)
        history = candidate.consolidation_history or ()
        if not history:
            await workflow._update(index, consolidation_applied=True)
            return False
        if candidate.consolidation is None:
            value = CandidateConsolidationInput(
                family=candidate.spec.family,
                purpose=candidate.spec.purpose,
                candidate=item,
                previous_artifacts=history,
            )
            workflow._check_input(value, candidate.consolidation_messages)
            try:
                result = await workflow._call(
                    lambda limit: workflow.generator.consolidate(
                        value, messages=candidate.consolidation_messages, max_requests=limit
                    ),
                    stage="reviewing",
                )
            except InferenceError as error:
                await workflow._update(index, consolidation_messages=error.messages or candidate.consolidation_messages)
                raise
            candidate = await workflow._update(
                index, consolidation=result.output, consolidation_messages=result.messages
            )
        return await self._apply_decision(index)

    async def _apply_decision(self, index: int) -> bool:
        workflow = self.workflow
        candidate = workflow.record.candidates[index]
        history = candidate.consolidation_history or ()
        decision = candidate.consolidation
        if decision is None:
            raise TraceLearningError("invalid_checkpoint")
        existing = next((old for old in history if old.key == candidate.spec.key), None)
        if decision.target is None:
            if existing is not None:
                # A known key cannot silently overwrite a different capability.
                raise TraceLearningError("capability_identity_conflict")
            await workflow._update(index, consolidation_applied=True)
            return False
        target = next((old for old in history if old.ref == decision.target), None)
        if target is None or target.ref.family != candidate.spec.family:
            raise TraceLearningError("invalid_consolidation_target")
        if existing is not None and existing.ref.artifact_id != target.ref.artifact_id:
            raise TraceLearningError("historical_identity_merge")
        trace_ids = tuple(
            dict.fromkeys((
                *candidate.spec.trace_ids,
                *(
                    trace_id
                    for sibling in workflow.record.candidates
                    if sibling.spec.family == candidate.spec.family
                    and sibling.spec.key == target.key
                    and sibling.outcome.status == "published"
                    for trace_id in sibling.spec.trace_ids
                ),
            ))
        )
        spec = candidate.spec.model_copy(
            update={
                "key": target.key,
                "trace_ids": trace_ids,
                "skill_reuse": (
                    SkillReuseDecision(ref=target.ref, same_method_reason=decision.reason)
                    if candidate.spec.family == "skill"
                    else None
                ),
            }
        )
        feedback = CandidateFeedback(
            consolidation=ConsolidationFeedback(previous=target, reason=decision.reason, guidance=decision.guidance)
        )
        candidates = list(workflow.record.candidates)
        candidates[index] = candidate.model_copy(
            update={
                "spec": spec,
                "history": history,
                "feedback": feedback,
                "consolidation_applied": True,
                "review_resolved": False,
                "outcome": candidate.outcome.model_copy(update={"status": "repairing", "reason": None}),
            }
        )
        workflow.record = workflow.record.model_copy(update={"candidates": tuple(candidates)})
        # Key redirection, target choice and the need to regenerate share one durable checkpoint.
        await workflow._store()
        return True


def redirect_tool_dependencies(candidates: list[LearningCandidate], candidate: LearningCandidate) -> None:
    """Redirect only in the transaction publishing the verified replacement Tool."""
    original_key, target_key = candidate.outcome.key, candidate.spec.key
    if candidate.spec.family != "tool" or original_key == target_key:
        return
    for index, sibling in enumerate(candidates):
        if sibling.spec.family != "skill" or original_key not in sibling.spec.tool_keys:
            continue
        if sibling.revisions or sibling.outcome.status == "published":
            raise TraceLearningError("consolidation_dependency_already_generated")
        tool_keys = tuple(dict.fromkeys(target_key if key == original_key else key for key in sibling.spec.tool_keys))
        candidates[index] = sibling.model_copy(
            update={"spec": sibling.spec.model_copy(update={"tool_keys": tool_keys})}
        )
