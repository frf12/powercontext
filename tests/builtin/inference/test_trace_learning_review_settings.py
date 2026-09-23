"""Per-review controls keep candidate generation and persisted identities stable."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.tool import SqlToolImplementation, ToolContent
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.trace_learning.generation import (
    CandidateDiscoveryInput,
    CandidateLearningGenerator,
    open_trace_learning_generator,
)
from powercontext.builtin.trace_learning.models import LearningBudget


@pytest.mark.parametrize("review_max", [512, 99999])
def test_review_overrides_are_isolated_and_share_the_output_ceiling(monkeypatch, review_max):
    observed = []
    review_attempts = 0

    async def respond(messages, info):
        nonlocal review_attempts
        observed.append(dict(info.model_settings or {}))
        prompt = next(
            part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart)
        )
        value = json.loads(prompt)
        if "tool" in value:
            review_attempts += 1
            output = {"findings": "invalid-shape" if review_attempts == 1 else []}
        else:
            output = {"candidates": []}
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    async def open_model(*args, **kwargs):
        return None, FunctionModel(respond)

    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", open_model)

    async def scenario():
        config = InferenceConfig(
            generation_model="openai:test",
            generation_model_settings={"temperature": 0.25, "openai_reasoning_effort": "high"},
            trace_learning_tool_review_model_settings={"openai_reasoning_effort": "low", "max_tokens": review_max},
        )
        async with AsyncExitStack() as resources:
            generator = await open_trace_learning_generator(config, LearningBudget(max_output_tokens=1024), resources)
            assert isinstance(generator, CandidateLearningGenerator)
            await generator.discover(CandidateDiscoveryInput(max_candidates_per_family=2), max_requests=1)
            await generator.review_tool(
                ToolContent(
                    name="count_rows",
                    description="Count rows in the selected table.",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={"type": "object"},
                    implementation=SqlToolImplementation(
                        sql="SELECT COUNT(*) AS total FROM items",
                        parameter_order=(),
                        dialect="sqlite",
                        database_name="example",
                    ),
                ),
                max_requests=2,
            )
            await generator.discover(CandidateDiscoveryInput(max_candidates_per_family=2), max_requests=1)
        assert config.generation_model_settings["openai_reasoning_effort"] == "high"
        assert [settings["openai_reasoning_effort"] for settings in observed] == ["high", "low", "low", "high"]
        assert all(settings["temperature"] == 0.25 for settings in observed)
        assert [settings["max_tokens"] for settings in observed] == [
            1024,
            min(review_max, 1024),
            min(review_max, 1024),
            1024,
        ]

    asyncio.run(scenario())


def test_default_review_settings_preserve_persisted_generation_identity(monkeypatch):
    async def open_model(*args, **kwargs):
        return None, FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("{}")]))

    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", open_model)

    async def scenario():
        config = InferenceConfig(generation_model="openai:test")
        # This is the identity persisted by existing v4 learning checkpoints.
        old_identity = content_digest(
            config.model_dump_json(
                include={
                    "generation_model",
                    "generation_base_url",
                    "generation_model_settings",
                    "generation_timeout_seconds",
                    "generation_max_requests",
                    "generation_allow_python_literals",
                }
            ).encode()
        )
        async with AsyncExitStack() as resources:
            original = await open_trace_learning_generator(config, LearningBudget(), resources)
            changed = await open_trace_learning_generator(
                config.model_copy(
                    update={"trace_learning_tool_review_model_settings": {"openai_reasoning_effort": "low"}}
                ),
                LearningBudget(),
                resources,
            )
        assert original is not None and changed is not None
        assert original.config_id == old_identity
        assert changed.config_id != old_identity

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "override", [{"extra_headers": {"authorization": "not-a-secret"}}, {"max_tokens": 0}, {"max_tokens": True}]
)
def test_invalid_review_settings_are_rejected(override):
    with pytest.raises(ValidationError):
        InferenceConfig(generation_model="openai:test", trace_learning_tool_review_model_settings=override)


def test_review_settings_require_a_generation_model():
    with pytest.raises(ValidationError):
        InferenceConfig(trace_learning_tool_review_model_settings={"openai_reasoning_effort": "low"})
