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

"""Model-backed structured generation for explicitly imported trace bundles."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.builtin.artifacts.tool import ToolContent
from powercontext.builtin.inference.models import GenerationResult
from powercontext.builtin.inference.protocols import StructuredGenerator
from powercontext.builtin.trace_learning.models import (
    CandidateFeedback,
    CandidateInventory,
    CandidateResponse,
    CandidateSpec,
    GeneratedCandidate,
    GeneratedExperience,
    GeneratedSkill,
    GeneratedTool,
    GeneratedTraceLearningBundle,
    LearningBudget,
    ToolReview,
    TraceLearningGenerationInput,
)
from powercontext.builtin.trace_learning.prompts import (
    CANDIDATE_DISCOVERY_INSTRUCTIONS,
    CANDIDATE_GENERATION_INSTRUCTIONS,
    TOOL_REVIEW_INSTRUCTIONS,
    TRACE_LEARNING_INSTRUCTIONS,
)

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.models.instrumented import InstrumentationSettings
    from pydantic_ai.settings import ModelSettings

    from powercontext.builtin.inference.pydantic_ai import InferenceLimits
    from powercontext.builtin.runtime.config import InferenceConfig


class TraceLearningGenerator(Protocol):
    config_id: str

    async def generate(
        self,
        value: TraceLearningGenerationInput,
        /,
    ) -> GenerationResult[GeneratedTraceLearningBundle]: ...


class LLMTraceLearningGenerator:
    def __init__(
        self,
        generator: StructuredGenerator[TraceLearningGenerationInput, GeneratedTraceLearningBundle],
        *,
        config_id: str,
    ) -> None:
        self._generator = generator
        self.config_id = config_id

    async def generate(self, value: TraceLearningGenerationInput, /) -> GenerationResult[GeneratedTraceLearningBundle]:
        return await self._generator.generate(value)


class CandidateDiscoveryInput(BaseModel):
    phase: Literal["discover"] = "discover"
    context: TraceLearningGenerationInput
    max_candidates_per_family: int


class CandidateGenerationInput(BaseModel):
    phase: Literal["generate"] = "generate"
    candidate: CandidateSpec
    context: TraceLearningGenerationInput | None = None
    available_tools: tuple[GeneratedTool, ...] = ()
    feedback: CandidateFeedback | None = None


class ToolReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: ToolContent


class CandidateLearningGenerator(LLMTraceLearningGenerator):
    """Independent discovery, per-candidate conversations, and tool-only review."""

    def __init__(
        self,
        *,
        model: Model,
        limits: InferenceLimits,
        config_id: str,
        model_settings: ModelSettings | None = None,
        legacy_config_id: str | None = None,
    ) -> None:
        from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator

        self.max_requests = limits.max_requests
        self.legacy_config_id = legacy_config_id
        super().__init__(
            PydanticAIStructuredGenerator(
                model=model,
                limits=limits.model_copy(update={"max_requests": 1}),
                model_settings=model_settings,
                instructions=TRACE_LEARNING_INSTRUCTIONS,
                input_type=TraceLearningGenerationInput,
                output_type=GeneratedTraceLearningBundle,
            ),
            config_id=config_id,
        )
        self._discovery = PydanticAIStructuredGenerator(
            model=model,
            limits=limits,
            model_settings=model_settings,
            instructions=CANDIDATE_DISCOVERY_INSTRUCTIONS,
            input_type=CandidateDiscoveryInput,
            output_type=CandidateInventory,
            name="trace_learning_discovery",
        )
        self._candidates = {
            family: PydanticAIStructuredGenerator(
                model=model,
                limits=limits,
                model_settings=model_settings,
                instructions=CANDIDATE_GENERATION_INSTRUCTIONS,
                input_type=CandidateGenerationInput,
                output_type=CandidateResponse[kind],
                name="trace_learning_" + family,
            )
            for family, kind in (
                ("experience", GeneratedExperience),
                ("tool", GeneratedTool),
                ("skill", GeneratedSkill),
            )
        }
        self._reviewer = PydanticAIStructuredGenerator(
            model=model,
            limits=limits,
            model_settings=model_settings,
            instructions=TOOL_REVIEW_INSTRUCTIONS,
            input_type=ToolReviewInput,
            output_type=ToolReview,
            name="trace_learning_tool_review",
        )

    async def discover(self, value: CandidateDiscoveryInput, *, messages=(), max_requests: int):
        return await self._discovery.generate_conversation(value, messages=messages, max_requests=max_requests)

    async def generate_candidate(
        self,
        value: CandidateGenerationInput,
        *,
        messages: tuple[dict[str, JsonValue], ...],
        max_requests: int,
    ) -> GenerationResult[CandidateResponse[GeneratedCandidate]]:
        result = await self._candidates[value.candidate.family].generate_conversation(
            value,
            messages=messages,
            max_requests=max_requests,
        )
        return GenerationResult[CandidateResponse[GeneratedCandidate]].model_validate(result.model_dump())

    async def review_tool(self, tool: ToolContent, *, max_requests: int) -> GenerationResult[ToolReview]:
        # Never pass generation messages or trace examples to this separate conversation.
        return await self._reviewer.generate_conversation(ToolReviewInput(tool=tool), max_requests=max_requests)


async def open_trace_learning_generator(
    settings: InferenceConfig,
    budget: LearningBudget,
    resources: AsyncExitStack,
    instrumentation: InstrumentationSettings | None = None,
) -> TraceLearningGenerator | None:
    if settings.generation_model is None:
        return None
    from pydantic_ai.settings import ModelSettings

    from powercontext.builtin.evidence.models import content_digest
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits
    from powercontext.builtin.runtime.composition import _open_pydantic_ai_model

    _, model = await _open_pydantic_ai_model(
        settings.generation_model,
        base_url=settings.generation_base_url,
        headers=settings.generation_headers,
        resources=resources,
        instrumentation=instrumentation,
        disable_provider_retries=True,
    )
    model_settings = cast(ModelSettings, dict(settings.generation_model_settings))
    model_settings["max_tokens"] = min(
        int(model_settings.get("max_tokens") or budget.max_output_tokens), budget.max_output_tokens
    )
    identity = settings.model_dump_json(
        include={
            "generation_model",
            "generation_base_url",
            "generation_model_settings",
            "generation_timeout_seconds",
            "generation_max_requests",
            "generation_allow_python_literals",
        }
    )
    return CandidateLearningGenerator(
        model=model,
        model_settings=model_settings,
        limits=InferenceLimits(
            timeout_seconds=min(settings.generation_timeout_seconds, budget.timeout_seconds),
            max_requests=settings.generation_max_requests,
            max_output_tokens_per_request=budget.max_output_tokens,
            output_tokens_limit=budget.max_output_tokens * settings.generation_max_requests,
            allow_python_literals=settings.generation_allow_python_literals,
            allow_continuations=False,
        ),
        config_id=content_digest(identity.encode()),
        legacy_config_id=content_digest(
            settings.model_dump_json(
                include={
                    "generation_model",
                    "generation_base_url",
                    "generation_model_settings",
                    "generation_timeout_seconds",
                }
            ).encode()
        ),
    )
