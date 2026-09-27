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
from math import isfinite
from typing import TYPE_CHECKING, Literal, Protocol, cast

import sqlglot
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator
from sqlglot import exp

from powercontext.builtin.artifacts.tool import ToolContent
from powercontext.builtin.inference.models import GenerationResult
from powercontext.builtin.inference.protocols import StructuredGenerator
from powercontext.builtin.trace_learning.models import (
    CandidateFeedback,
    CandidateInventory,
    CandidateResponse,
    CandidateSpec,
    ConsolidationDecision,
    GeneratedCandidate,
    GeneratedExperience,
    GeneratedSkill,
    GeneratedTool,
    GeneratedTraceLearningBundle,
    LearningBudget,
    PreviousLearningArtifact,
    ReviewDecision,
    ToolReview,
    TraceLearningGenerationInput,
    TraceLearningHostProfile,
)
from powercontext.builtin.trace_learning.parameter_plan import (
    ToolParameterPlan,
    ToolPlanSource,
    assemble_parameter_plan,
)
from powercontext.builtin.trace_learning.prompts import (
    CANDIDATE_DISCOVERY_INSTRUCTIONS,
    CANDIDATE_GENERATION_INSTRUCTIONS,
    CONSOLIDATION_INSTRUCTIONS,
    TOOL_PARAMETER_PLAN_INSTRUCTIONS,
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
    context: TraceLearningGenerationInput | None = None
    max_candidates_per_family: int
    inventory: CandidateInventory | None = None
    feedback: tuple[str, ...] = ()


class CandidateGenerationInput(BaseModel):
    phase: Literal["generate"] = "generate"
    candidate: CandidateSpec
    context: TraceLearningGenerationInput | None = None
    available_tools: tuple[GeneratedTool, ...] = ()
    feedback: CandidateFeedback | None = None
    tool_sources: tuple[ToolPlanSource, ...] = ()
    host_profile: TraceLearningHostProfile | None = None


class ToolReviewFollowup(BaseModel):
    previous_tool: ToolContent
    review: ToolReview
    decisions: tuple[ReviewDecision, ...] = ()


class ToolLiteral(BaseModel):
    value: str
    kind: Literal["string", "number"]
    expression: str


class PropertySample(BaseModel):
    value: str | int | float | bool | None
    accepted: bool


class ToolPropertyProbe(BaseModel):
    parameter: str
    scope: Literal["property_schema_only"] = "property_schema_only"
    samples: tuple[PropertySample, ...]


class ToolReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: ToolContent
    followup: ToolReviewFollowup | None = None
    literals: tuple[ToolLiteral, ...] = ()
    property_probes: tuple[ToolPropertyProbe, ...] = ()


def _property_sample_values(schema: dict[str, JsonValue]) -> list[str | int | float | bool | None]:
    values: list[str | int | float | bool | None] = []
    match schema["type"]:
        case "integer" | "number":
            values.extend((-1, 0, 1))
            for key in ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum"):
                bound = schema.get(key)
                if isinstance(bound, (int, float)) and not isinstance(bound, bool):
                    values.extend((bound - 1, bound, bound + 1))
        case "string":
            values.extend(("", "x"))
            for key in ("minLength", "maxLength"):
                length = schema.get(key)
                if isinstance(length, int):
                    values.extend("x" * size for size in (length - 1, length, length + 1) if 0 <= size <= 256)
        case "boolean":
            values.extend((False, True))
        case "null":
            values.append(None)
    examples = schema.get("enum", [])
    if isinstance(examples, list):
        values.extend(value for value in examples[:3] if not isinstance(value, (list, dict)))
    return values


def _property_probes(tool: ToolContent) -> tuple[ToolPropertyProbe, ...]:
    """Report bounded schema observations, not inferred domains or SQL execution."""
    result = []
    properties = cast(dict[str, dict[str, JsonValue]], tool.input_schema["properties"])
    for name, schema in properties.items():
        values = _property_sample_values(schema)
        validator = Draft202012Validator(schema)
        # Keep true/1 distinct; these observations cover the property schema only.
        unique = {(type(value), value): value for value in values if not isinstance(value, float) or isfinite(value)}
        result.append(
            ToolPropertyProbe(
                parameter=name,
                samples=tuple(
                    PropertySample(value=value, accepted=validator.is_valid(value)) for value in unique.values()
                ),
            )
        )
    return tuple(result)


def tool_review_input(
    tool: ToolContent,
    *,
    previous_tool: ToolContent | None = None,
    previous_review: ToolReview | None = None,
    decisions: tuple[ReviewDecision, ...] = (),
) -> ToolReviewInput:
    """Expose only executable contracts and review evidence, never teaching context."""
    tree = sqlglot.parse_one(tool.implementation.sql, read=tool.implementation.dialect)
    return ToolReviewInput(
        tool=tool,
        followup=None
        if previous_tool is None or previous_review is None
        else ToolReviewFollowup(previous_tool=previous_tool, review=previous_review, decisions=decisions),
        literals=tuple(
            ToolLiteral(
                value=str(node.this),
                kind="string" if node.is_string else "number",
                expression=(node.parent or node).sql(dialect=tool.implementation.dialect)[:500],
            )
            for node in tree.find_all(exp.Literal)
        ),
        property_probes=_property_probes(tool),
    )


class CandidateConsolidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["consolidate"] = "consolidate"
    family: Literal["experience", "tool", "skill"]
    purpose: str
    candidate: GeneratedCandidate
    previous_artifacts: tuple[PreviousLearningArtifact, ...]


class CandidateLearningGenerator(LLMTraceLearningGenerator):
    """Independent discovery, per-candidate conversations, and tool-only review."""

    def __init__(
        self,
        *,
        model: Model,
        limits: InferenceLimits,
        config_id: str,
        model_settings: ModelSettings | None = None,
        review_model_settings: ModelSettings | None = None,
        legacy_config_id: str | None = None,
        parameter_plans: bool = False,
    ) -> None:
        from pydantic_ai.settings import merge_model_settings

        from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator

        self.max_requests = limits.max_requests
        self.legacy_config_id = legacy_config_id
        self.parameter_plans = parameter_plans
        self._plan_model, self._plan_limits, self._plan_settings = model, limits, model_settings
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
        self._consolidator = PydanticAIStructuredGenerator(
            model=model,
            limits=limits,
            model_settings=model_settings,
            instructions=CONSOLIDATION_INSTRUCTIONS,
            input_type=CandidateConsolidationInput,
            output_type=ConsolidationDecision,
            name="trace_learning_consolidation",
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
        review_limits = limits
        if review_model_settings is not None and review_model_settings.get("max_tokens") is not None:
            review_max = review_model_settings["max_tokens"]
            if limits.max_output_tokens_per_request is not None:
                review_max = min(review_max, limits.max_output_tokens_per_request)
            review_limits = limits.model_copy(update={"max_output_tokens_per_request": review_max})
        self._reviewer = PydanticAIStructuredGenerator(
            model=model,
            limits=review_limits,
            model_settings=merge_model_settings(model_settings, review_model_settings),
            instructions=TOOL_REVIEW_INSTRUCTIONS,
            input_type=ToolReviewInput,
            output_type=ToolReview,
            name="trace_learning_tool_review",
        )

    async def discover(self, value: CandidateDiscoveryInput, *, messages=(), max_requests: int):
        return await self._discovery.generate_conversation(value, messages=messages, max_requests=max_requests)

    async def consolidate(self, value: CandidateConsolidationInput, *, messages=(), max_requests: int):
        return await self._consolidator.generate_conversation(value, messages=messages, max_requests=max_requests)

    async def generate_candidate(
        self,
        value: CandidateGenerationInput,
        *,
        messages: tuple[dict[str, JsonValue], ...],
        max_requests: int,
    ) -> GenerationResult[CandidateResponse[GeneratedCandidate]]:
        if value.tool_sources:
            return await self._generate_tool_plan(value, messages=messages, max_requests=max_requests)
        result = await self._candidates[value.candidate.family].generate_conversation(
            value,
            messages=messages,
            max_requests=max_requests,
        )
        return GenerationResult[CandidateResponse[GeneratedCandidate]].model_validate(result.model_dump())

    async def _generate_tool_plan(self, value: CandidateGenerationInput, *, messages, max_requests: int):
        from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator

        host = value.host_profile
        if host is None:
            raise ValueError("Tool planning requires a host profile")  # noqa: TRY003

        class PlannedResponse(CandidateResponse[ToolParameterPlan]):
            @model_validator(mode="after")
            def validate_compilation(self):
                if self.candidate.key != value.candidate.key:
                    raise ValueError("Preserve the requested candidate key")  # noqa: TRY003
                assemble_parameter_plan(self.candidate, value.tool_sources, host)
                return self

        planner = PydanticAIStructuredGenerator(
            model=self._plan_model,
            limits=self._plan_limits,
            model_settings=self._plan_settings,
            instructions=TOOL_PARAMETER_PLAN_INSTRUCTIONS,
            input_type=CandidateGenerationInput,
            output_type=PlannedResponse,
            name="trace_learning_tool_parameter_plan",
        )
        result = await planner.generate_conversation(value, messages=messages, max_requests=max_requests)
        output = result.output
        compiled = assemble_parameter_plan(output.candidate, value.tool_sources, host)
        return GenerationResult[CandidateResponse[GeneratedCandidate]](
            output=CandidateResponse(
                candidate=compiled,
                decisions=output.decisions,
                self_pass=output.self_pass,
                consolidation_conflict=output.consolidation_conflict,
            ),
            usage=result.usage,
            messages=result.messages,
        )

    async def review_tool(
        self,
        tool: ToolContent,
        *,
        max_requests: int,
        previous_tool: ToolContent | None = None,
        previous_review: ToolReview | None = None,
        decisions: tuple[ReviewDecision, ...] = (),
    ) -> GenerationResult[ToolReview]:
        # Never pass generation messages or trace examples to this separate conversation.
        value = tool_review_input(
            tool, previous_tool=previous_tool, previous_review=previous_review, decisions=decisions
        )
        return await self._reviewer.generate_conversation(value, max_requests=max_requests)


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
    identity_fields = {
        "generation_model",
        "generation_base_url",
        "generation_model_settings",
        "generation_timeout_seconds",
        "generation_max_requests",
        "generation_allow_python_literals",
    }
    # Empty overrides retain the identity stored by existing learning checkpoints.
    if settings.trace_learning_tool_review_model_settings:
        identity_fields.add("trace_learning_tool_review_model_settings")
    identity = settings.model_dump_json(include=identity_fields)
    return CandidateLearningGenerator(
        model=model,
        parameter_plans=True,
        model_settings=model_settings,
        review_model_settings=cast(ModelSettings, dict(settings.trace_learning_tool_review_model_settings)),
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
