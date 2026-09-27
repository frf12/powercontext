"""Learning reconciles capability identities before publishing redundant artifacts."""

from __future__ import annotations

import asyncio
import json

import pytest

from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.trace_learning.models import (
    GeneratedTraceLearningBundle,
    ImportTraceLearningRequest,
    LearningBudget,
)
from tests.e2e.test_trace_learning import bundle_data, candidate_spec, invoke, request_data, setup_service


def consolidation_generator(specs, merges, *, decide=None, transform=None, review=None):
    from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from powercontext.builtin.inference.pydantic_ai import InferenceLimits
    from powercontext.builtin.trace_learning.generation import CandidateLearningGenerator

    seen = []

    def respond(messages, info):
        value = json.loads(
            next(part.content for msg in reversed(messages) for part in msg.parts if isinstance(part, UserPromptPart))
        )
        seen.append(value)
        if "tool" in value:
            output = {"findings": [] if review is None else review(value["tool"])}
        elif value["phase"] == "discover":
            output = {"candidates": specs}
        elif value["phase"] == "consolidate":
            key = merges.get(value["candidate"]["key"], value["candidate"]["key"])
            target = next((item for item in value["previous_artifacts"] if item["key"] == key), None)
            output = {
                "target": None if target is None else target["ref"],
                "reason": "Same country-filtered station counting capability." if target else "A distinct method.",
                "guidance": "Preserve the old method and add current-count verification." if target else "",
            }
            if decide is not None:
                output = decide(value, output)
        else:
            spec = value["candidate"]
            item = bundle_data()[{"experience": "experiences", "tool": "tools", "skill": "skills"}[spec["family"]]][0]
            item["key"] = spec["key"]
            if spec["family"] == "experience" and (value.get("feedback") or {}).get("consolidation"):
                item["content"]["lesson"] += " Recheck the current count."
            if spec["family"] == "skill":
                item["tool_keys"] = spec["tool_keys"]
            if transform is not None:
                item = transform(value, item)
            findings = ((value.get("feedback") or {}).get("review") or {}).get("findings", [])
            output = {
                "candidate": item,
                "decisions": [
                    {"finding_id": finding["id"], "decision": "accept", "reason": "Clarified the callable contract."}
                    for finding in findings
                ],
            }
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    return CandidateLearningGenerator(
        model=FunctionModel(respond), limits=InferenceLimits(max_requests=2), config_id="consolidation-tests"
    ), seen


def test_same_run_equivalent_experiences_update_one_identity(tmp_path):
    async def scenario():
        generator, seen = consolidation_generator(
            [candidate_spec("experience", "station-country"), candidate_spec("experience", "station-country-copy")],
            {"station-country-copy": "station-country"},
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert len(done.artifacts) == 1
            assert done.artifacts[0].revision == 2
            async with service.database.transaction() as connection:
                artifact = await service.contexts.repositories.artifacts.get(connection, "learning", done.artifacts[0])
            assert "Recheck the current count" in artifact.content.lesson
            assert done.usage.model_calls == len(seen)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_related_but_distinct_lessons_are_preserved(tmp_path):
    async def scenario():
        generator, seen = consolidation_generator(
            [candidate_spec("experience", "station-country"), candidate_spec("experience", "station-precision")],
            {},
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 2
            assert all(ref.revision == 1 for ref in done.artifacts)
            assert any(item.get("phase") == "consolidate" for item in seen)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad_ref",
    [
        {"family": "experience", "artifact_id": "unseen", "revision": 1},
        {"family": "tool", "artifact_id": "unseen", "revision": 1},
    ],
)
def test_unknown_consolidation_target_cannot_publish_or_overwrite(tmp_path, bad_ref):
    async def scenario():
        generator, _ = consolidation_generator(
            [candidate_spec("experience", "station-country"), candidate_spec("experience", "station-copy")],
            {},
            decide=lambda value, decision: decision | {"target": bad_ref},
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 1 and done.artifacts[0].revision == 1
            assert done.candidate_outcomes[1].reason == "invalid_consolidation_target"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_restart_after_consolidation_keeps_decision_and_generating_conversation(tmp_path, monkeypatch):
    from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled

    async def scenario():
        generator, seen = consolidation_generator(
            [candidate_spec("experience", "station-country"), candidate_spec("experience", "station-copy")],
            {"station-copy": "station-country"},
        )
        generate = generator.generate_candidate
        interrupted = False

        async def interrupt(value, *, messages, max_requests):
            nonlocal interrupted
            if value.feedback is not None and value.feedback.consolidation is not None:
                assert messages
                if not interrupted:
                    interrupted = True
                    raise InvocationAlreadyHandled()
            return await generate(value, messages=messages, max_requests=max_requests)

        monkeypatch.setattr(generator, "generate_candidate", interrupt)
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            with pytest.raises(InvocationAlreadyHandled):
                await invoke(service)
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 1 and done.artifacts[0].revision == 2
            assert sum(item.get("phase") == "consolidate" for item in seen) == 1
            assert done.usage.model_calls - done.usage.reserved_model_calls == len(seen)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_tool_merge_cannot_silently_narrow_the_existing_parameter_contract(tmp_path):
    def narrow(value, item):
        if (value.get("feedback") or {}).get("consolidation"):
            item["content"]["input_schema"]["properties"]["country"]["enum"] = ["CZE"]
        return item

    async def scenario():
        generator, _ = consolidation_generator(
            [
                candidate_spec("tool", "count-country"),
                candidate_spec("tool", "count-copy"),
                candidate_spec("skill", "count-stations") | {"tool_keys": ["count-copy"]},
            ],
            {"count-copy": "count-country"},
            transform=narrow,
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 1 and done.artifacts[0].revision == 1
            assert done.candidate_outcomes[1].reason == "validation_failed"
            assert done.candidate_outcomes[2].reason == "unavailable_tool_dependency"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_merged_tool_redirects_skill_to_verified_current_revision(tmp_path):
    async def scenario():
        skill = candidate_spec("skill", "count-stations") | {"tool_keys": ["count-country-copy"]}
        generator, seen = consolidation_generator(
            [candidate_spec("tool", "count-country"), candidate_spec("tool", "count-country-copy"), skill],
            {"count-country-copy": "count-country"},
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert all(item.status == "published" for item in done.candidate_outcomes), done.candidate_outcomes
            tools = [ref for ref in done.artifacts if ref.family == "tool"]
            assert len(tools) == 1 and tools[0].revision == 2
            async with service.database.transaction() as connection:
                skill_artifact = await service.contexts.repositories.artifacts.get(
                    connection, "learning", next(ref for ref in done.artifacts if ref.family == "skill")
                )
            assert skill_artifact.content.tool_dependencies == tuple(tools)
            skill_inputs = [
                item for item in seen if item.get("phase") == "generate" and item["candidate"]["family"] == "skill"
            ]
            assert skill_inputs[0]["candidate"]["tool_keys"] == ["count-country"]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_learning_retrieves_relevant_history_beyond_recent_catalog(tmp_path):
    class SeedGenerator:
        config_id = "seed"

        async def generate(self, value):
            item = bundle_data()["experiences"][0]
            if value.traces[0].trace_id == "unrelated":
                item = {
                    "key": "network-routing",
                    "content": {
                        "situation": "Network routing",
                        "action": "Inspect packet routes",
                        "outcome": "Resolved connectivity",
                        "lesson": "Check routing paths",
                    },
                }
            return GenerationResult(
                output=GeneratedTraceLearningBundle.model_validate({"experiences": [item]}),
                usage=InferenceUsage(requests=1),
            )

    async def scenario():
        manager, service = await setup_service(tmp_path, SeedGenerator())
        try:
            for trace_id in ("old-relevant", "unrelated"):
                await service.import_traces(
                    "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data(trace_id, trace_id))
                )
                await invoke(service)
            generator, seen = consolidation_generator(
                [candidate_spec("experience", "station-country-copy")], {"station-country-copy": "station-country"}
            )
            service.generator = generator
            service.budget = LearningBudget(previous_artifact_limit=1)
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            first_generation = next(item for item in seen if item.get("phase") == "generate")
            assert [item["key"] for item in first_generation["context"]["previous_artifacts"]] == ["station-country"]
            assert done.artifacts[0].revision == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["revise", "retire"])
def test_target_changed_after_decision_cannot_be_overwritten_or_recreated(tmp_path, monkeypatch, change):
    from sqlalchemy import update

    from powercontext.builtin.artifacts.experience import ExperienceDraft
    from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

    async def scenario():
        generator, _ = consolidation_generator(
            [candidate_spec("experience", "station-country"), candidate_spec("experience", "station-copy")],
            {"station-copy": "station-country"},
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        consolidate = generator.consolidate

        async def alter_target(value, *, messages, max_requests):
            result = await consolidate(value, messages=messages, max_requests=max_requests)
            ref = result.output.target
            async with service.database.transaction() as connection:
                if change == "revise":
                    old = await service.contexts.repositories.artifacts.get(connection, "learning", ref)
                    await service.contexts.repositories.artifacts.revise(
                        connection,
                        "learning",
                        old,
                        ExperienceDraft(content=old.content.model_copy(update={"lesson": "Concurrent correction."})),
                    )
                else:
                    await connection.execute(
                        update(ARTIFACT_HEADS_TABLE)
                        .where(
                            ARTIFACT_HEADS_TABLE.c.scope_id == "learning",
                            ARTIFACT_HEADS_TABLE.c.artifact_id == ref.artifact_id,
                        )
                        .values(lifecycle_state="retired")
                    )
            return result

        monkeypatch.setattr(generator, "consolidate", alter_target)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 1
            assert done.candidate_outcomes[1].status != "published"
            async with service.database.transaction() as connection:
                revisions = await service.contexts.repositories.artifacts.revisions(
                    connection, "learning", "experience", done.artifacts[0].artifact_id
                )
            assert len(revisions) == (2 if change == "revise" else 1)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_learning_uses_persistent_vectors_for_cross_language_history(tmp_path):
    from tests.builtin.runtime.test_learned_hybrid_recall import _import_trace, _StationEmbeddingModel
    from tests.e2e.test_trace_learning import Generator
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        embedding = _StationEmbeddingModel()
        async with learning_http(tmp_path, Generator(), embedding_model=embedding) as env:
            old = await _import_trace(env)
            spec = candidate_spec("experience", "station-copy") | {"purpose": "按国家统计加油站数量"}
            generator, seen = consolidation_generator([spec], {"station-copy": "station-country"})
            service = env.runtime._trace_learning_service
            service.generator = generator
            service.budget = LearningBudget()
            data = request_data("chinese")
            data["traces"][0]["question"] = "按国家统计加油站数量"
            accepted = await env.client.post("/v1/scopes/learning/trace-learning", json=data)
            assert accepted.status_code == 202, accepted.text
            await env.controller.process()
            response = await env.client.get(f"/v1/scopes/learning/trace-learning/{accepted.json()['run_id']}")
            done = response.json()
            assert done["status"] == "succeeded", done
            first = next(item for item in seen if item.get("phase") == "generate")
            assert [item["key"] for item in first["context"]["previous_artifacts"]] == ["station-country"]
            old_ref = next(ref for ref in old["artifacts"] if ref["family"] == "experience")
            assert done["artifacts"] == [old_ref | {"revision": old_ref["revision"] + 1}]
            assert any("按国家统计加油站数量" in texts for texts in embedding.calls)

    asyncio.run(scenario())


def test_generating_agent_can_dispute_a_consolidation_without_overwriting_history(tmp_path, monkeypatch):
    async def scenario():
        generator, _ = consolidation_generator(
            [candidate_spec("experience", "station-country"), candidate_spec("experience", "station-copy")],
            {"station-copy": "station-country"},
        )
        generate = generator.generate_candidate

        async def dispute(value, *, messages, max_requests):
            result = await generate(value, messages=messages, max_requests=max_requests)
            if value.feedback is not None and value.feedback.consolidation is not None:
                result = result.model_copy(
                    update={
                        "output": result.output.model_copy(
                            update={
                                "consolidation_conflict": "This lesson describes a separate decision, not a count refinement."
                            }
                        )
                    }
                )
            return result

        monkeypatch.setattr(generator, "generate_candidate", dispute)
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 1 and done.artifacts[0].revision == 1
            assert done.candidate_outcomes[1].status == "deferred"
            assert done.candidate_outcomes[1].reason == "consolidation_disputed"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_review_repairs_keep_both_findings_and_consolidation_guidance(tmp_path):
    def transform(value, item):
        feedback = value.get("feedback") or {}
        if not feedback.get("consolidation") or feedback.get("review"):
            item["content"]["input_schema"]["properties"]["country"]["description"] = "Country code to count."
        return item

    def review(tool):
        return (
            []
            if tool["input_schema"]["properties"]["country"].get("description")
            else [
                {
                    "id": "country-meaning",
                    "category": "input_contract",
                    "comment": "Missing parameter explanation.",
                    "suggestion": "Describe the country code.",
                }
            ]
        )

    async def scenario():
        generator, seen = consolidation_generator(
            [candidate_spec("tool", "count-country"), candidate_spec("tool", "count-copy")],
            {"count-copy": "count-country"},
            transform=transform,
            review=review,
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget()
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert len(done.artifacts) == 1 and done.artifacts[0].revision == 2
            repairs = [
                value
                for value in seen
                if value.get("phase") == "generate"
                and ((value.get("feedback") or {}).get("review") or {}).get("findings")
            ]
            assert repairs and all(value["feedback"].get("consolidation") for value in repairs)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
