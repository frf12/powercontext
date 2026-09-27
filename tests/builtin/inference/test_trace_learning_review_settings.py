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


def test_followup_review_receives_tool_changes_and_findings_without_teaching_evidence():
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits
    from powercontext.builtin.trace_learning.models import ReviewDecision, ToolReview

    received = []

    def respond(messages, info):
        prompt = next(
            part.content for message in reversed(messages) for part in message.parts if isinstance(part, UserPromptPart)
        )
        received.append(json.loads(prompt))
        return ModelResponse(parts=[TextPart('{"findings": []}')])

    async def scenario():
        tool = ToolContent(
            name="list_items",
            description="List up to six items.",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            implementation=SqlToolImplementation(
                sql="SELECT id FROM items LIMIT 6", parameter_order=(), dialect="sqlite", database_name="example"
            ),
        )
        review = ToolReview.model_validate({
            "findings": [
                {
                    "id": "row-limit",
                    "category": "parameterization",
                    "comment": "Six is a result-size input.",
                    "suggestion": "Assess a bounded count parameter.",
                }
            ]
        })
        generator = CandidateLearningGenerator(
            model=FunctionModel(respond), limits=InferenceLimits(max_requests=1), config_id="review-followup"
        )
        await generator.review_tool(
            tool,
            max_requests=1,
            previous_tool=tool,
            previous_review=review,
            decisions=(ReviewDecision(finding_id="row-limit", decision="reject", reason="Algorithm bound is six."),),
        )
        value = received[0]
        assert value["followup"]["review"]["findings"][0]["id"] == "row-limit"
        assert value["followup"]["decisions"][0]["decision"] == "reject"
        assert any(item["value"] == "6" for item in value["literals"])
        assert "traces" not in value and "context" not in value and "messages" not in value

    asyncio.run(scenario())


def test_review_exposes_actual_property_validation_without_inventing_a_domain():
    from powercontext.builtin.trace_learning.generation import tool_review_input

    def review_input(**constraints):
        return tool_review_input(
            ToolContent(
                name="list_items",
                description="List a positive number of items.",
                input_schema={
                    "type": "object",
                    "properties": {"count": {"type": "integer", "description": "Must be positive", **constraints}},
                    "required": ["count"],
                    # Probes must not claim to cover root-level constraints or a complete invocation.
                    "allOf": [{"properties": {"count": {"maximum": 5}}}],
                },
                output_schema={"type": "object"},
                implementation=SqlToolImplementation(
                    sql="SELECT id FROM items LIMIT ?", parameter_order=("count",), dialect="sqlite"
                ),
            )
        ).model_dump(mode="json")

    missing = review_input()["property_probes"]
    bounded = review_input(minimum=1, maximum=10)["property_probes"]
    assert missing[0]["parameter"] == "count"
    assert missing[0]["scope"] == "property_schema_only"
    assert {sample["value"]: sample["accepted"] for sample in missing[0]["samples"]} == {
        -1: True,
        0: True,
        1: True,
    }
    tested = {sample["value"]: sample["accepted"] for sample in bounded[0]["samples"]}
    assert tested[0] is False and tested[1] is True
    assert tested[10] is True and tested[11] is False


def test_review_probes_report_string_constraints_and_boolean_domains():
    from powercontext.builtin.trace_learning.generation import tool_review_input

    value = tool_review_input(
        ToolContent(
            name="filter_items",
            description="Filter items by their code and enabled flag.",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "enum": ["active", "archived"]},
                    "enabled": {"type": "boolean"},
                },
                "required": ["code", "enabled"],
            },
            output_schema={"type": "object"},
            implementation=SqlToolImplementation(
                sql="SELECT id FROM items WHERE code = ? AND enabled = ?",
                parameter_order=("code", "enabled"),
                dialect="sqlite",
            ),
        )
    ).model_dump(mode="json")
    by_name = {probe["parameter"]: probe["samples"] for probe in value["property_probes"]}
    tested = {sample["value"]: sample["accepted"] for sample in by_name["code"]}
    assert tested[""] is False and tested["active"] is True
    assert all(sample["accepted"] for sample in by_name["enabled"])
