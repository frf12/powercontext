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
from typing import TYPE_CHECKING, Protocol, cast

from powercontext.builtin.inference.models import GenerationResult
from powercontext.builtin.inference.protocols import StructuredGenerator
from powercontext.builtin.trace_learning.models import (
    GeneratedTraceLearningBundle,
    LearningBudget,
    TraceLearningGenerationInput,
)
from powercontext.builtin.trace_learning.prompts import TRACE_LEARNING_INSTRUCTIONS

if TYPE_CHECKING:
    from pydantic_ai.models.instrumented import InstrumentationSettings

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
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
    from powercontext.builtin.inference.usage import UsageReportingStructuredGenerator
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
    generator = PydanticAIStructuredGenerator(
        model=model,
        instructions=TRACE_LEARNING_INSTRUCTIONS,
        input_type=TraceLearningGenerationInput,
        output_type=GeneratedTraceLearningBundle,
        limits=InferenceLimits(
            timeout_seconds=min(settings.generation_timeout_seconds, budget.timeout_seconds), max_requests=1
        ),
        model_settings=model_settings,
        name="trace_learning",
    )
    identity = settings.model_dump_json(
        include={
            "generation_model",
            "generation_base_url",
            "generation_model_settings",
            "generation_timeout_seconds",
        }
    )
    return LLMTraceLearningGenerator(
        UsageReportingStructuredGenerator(generator), config_id=content_digest(identity.encode())
    )
