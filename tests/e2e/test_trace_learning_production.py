"""Production learning can close advisory reviews without losing validated work."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.inference.pydantic_ai import InferenceLimits
from powercontext.builtin.trace_learning.generation import CandidateLearningGenerator
from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget
from tests.e2e.test_trace_learning import bundle_data, candidate_spec, invoke, request_data, setup_service


@pytest.mark.parametrize("invalid_sql", [False, True])
def test_mixed_review_self_pass_validates_the_latest_tool(tmp_path, invalid_sql):
    async def scenario():
        review_calls = []

        def respond(messages, info):
            value = json.loads(
                next(
                    part.content for msg in reversed(messages) for part in msg.parts if isinstance(part, UserPromptPart)
                )
            )
            if "tool" in value:
                review_calls.append(value)
                output = {
                    "findings": [
                        {
                            "id": "meaning",
                            "category": "input_contract",
                            "comment": "Missing parameter meaning.",
                            "suggestion": "Describe country representation.",
                        },
                        {
                            "id": "view",
                            "category": "implementation",
                            "comment": "A view might be faster.",
                            "suggestion": "Create a view.",
                        },
                    ]
                }
            elif value["phase"] == "discover":
                output = {"candidates": [candidate_spec("tool", "count-country")]}
            else:
                item = bundle_data(wrong=bool(value.get("feedback")) and invalid_sql)["tools"][0]
                output = {"candidate": item}
                if value.get("feedback"):
                    item["content"]["input_schema"]["properties"]["country"]["description"] = (
                        "Stored country code, e.g. CZE."
                    )
                    output.update(
                        self_pass=True,
                        decisions=[
                            {
                                "finding_id": "meaning",
                                "decision": "accept",
                                "reason": "Added the representation and example.",
                            },
                            {
                                "finding_id": "view",
                                "decision": "reject",
                                "reason": "Performance redesign is outside this read-only tool's scope.",
                            },
                        ],
                    )
            return ModelResponse(parts=[TextPart(json.dumps(output))])

        generator = CandidateLearningGenerator(
            model=FunctionModel(respond), limits=InferenceLimits(max_requests=1), config_id="self-pass"
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=12, max_candidate_validation_repairs=1)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            outcome = done.candidate_outcomes[0]
            if invalid_sql:
                assert not done.artifacts
                assert outcome.reason == "validation_failed"
            else:
                assert outcome.status == "published", outcome
                assert len(review_calls) == 1  # Self-pass must not need the reviewer's consent.
                async with service.database.transaction() as connection:
                    record = await service.repository.get(connection, "learning", run.run_id)
                assert record.candidates[0].review_resolution == "generator_self_pass"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_core_tool_publishes_before_auxiliary_experience_exhausts_budget(tmp_path):
    async def scenario():
        from tests.e2e.test_trace_learning import candidate_generator_for

        generator, _ = candidate_generator_for([
            candidate_spec("experience", "station-country"),
            candidate_spec("tool", "count-country"),
        ])
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=3)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert [ref.family for ref in done.artifacts] == ["tool"]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("defect", ["literal", "schema"])
def test_parameter_plan_is_repaired_in_conversation_and_published(tmp_path, defect):
    async def scenario():
        plans = []

        def respond(messages, info):
            value = json.loads(
                next(
                    part.content for msg in reversed(messages) for part in msg.parts if isinstance(part, UserPromptPart)
                )
            )
            if "tool" in value:
                output = {"findings": []}
            elif value["phase"] == "discover":
                output = {"candidates": [candidate_spec("tool", "count-country")]}
            else:
                plans.append(value)
                source = value["tool_sources"][0]
                output = {
                    "candidate": {
                        "key": "count-country",
                        "source_id": source["id"],
                        "name": "count_stations",
                        "description": "Count stations by their stored country code.",
                        "output_schema": {"type": "object"},
                        "bindings": [
                            {
                                "name": "country",
                                "literal_ids": [
                                    "missing"
                                    if defect == "literal" and len(plans) == 1
                                    else source["literals"][0]["id"]
                                ],
                                "property_schema": {
                                    "type": "string",
                                    "description": "Stored country code, for example CZE.",
                                    **({"minLength": "bad"} if defect == "schema" and len(plans) == 1 else {}),
                                },
                                "reason": "The country selects the population.",
                            }
                        ],
                        "constants": [],
                    }
                }
            return ModelResponse(parts=[TextPart(json.dumps(output))])

        generator = CandidateLearningGenerator(
            model=FunctionModel(respond), limits=InferenceLimits(max_requests=2), config_id="plan", parameter_plans=True
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=8)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.candidate_outcomes[0].status == "published", done
            assert done.usage.model_calls == 4  # Discovery, invalid plan, repaired plan, independent review.
            async with service.database.transaction() as connection:
                record = await service.repository.get(connection, "learning", run.run_id)
            assert record.generated.tools[0].content.implementation.parameter_order == ("country",)
            expected = "Unknown or repeated literal ID" if defect == "literal" else "Invalid parameter schema"
            assert expected in str(record.candidates[0].messages)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_resuming_tool_also_releases_its_dependency_blocked_skill(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import LearningCandidateSelection, ResumeLearningRunRequest
        from tests.e2e.test_trace_learning import candidate_generator_for

        skill = {**candidate_spec("skill", "count-stations"), "tool_keys": ["count-country"]}
        finding = {
            "id": "view",
            "category": "implementation",
            "comment": "Use a view.",
            "suggestion": "Redesign query.",
        }
        generator, _ = candidate_generator_for(
            [candidate_spec("tool", "count-country"), skill], findings=[finding], reject_review=True
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=20, max_candidate_repair_rounds=0)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            before = await service.get_run("learning", "runtime", run.run_id)
            assert {item.reason for item in before.candidate_outcomes} == {
                "review_unresolved",
                "unavailable_tool_dependency",
            }
            await service.resume_run(
                "learning",
                "runtime",
                run.run_id,
                ResumeLearningRunRequest(
                    idempotency_key="tool-and-dependents",
                    candidates=(LearningCandidateSelection(family="tool", key="count-country"),),
                    additional_repair_rounds=1,
                    additional_model_calls=4,
                ),
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert all(item.status == "published" for item in done.candidate_outcomes), done
            assert {ref.family for ref in done.artifacts} == {"tool", "skill"}
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
