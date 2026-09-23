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

"""Trace learning through real shared persistence and fenced worker invocations."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest


def request_data(key="first", trace_id="trace-1", country="CZE"):
    return {
        "idempotency_key": key,
        "host_profile": {"kind": "datus", "dialect": "sqlite", "database_name": "cards"},
        "traces": [
            {
                "trace_id": trace_id,
                "question": f"How many stations are in {country}?",
                "tool_calls": [
                    {
                        "call_id": "call-1",
                        "name": "read_query",
                        "arguments": {
                            "sql": {
                                "CZE": "SELECT COUNT(*) AS total FROM stations WHERE country = 'CZE'",
                                "SVK": "SELECT COUNT(*) AS total FROM stations WHERE country = 'SVK'",
                            }[country]
                        },
                        "result": {"columns": ["total"], "rows": [[12]]},
                    }
                ],
                "final_answer": "12 stations.",
                "context": {},
            }
        ],
    }


def bundle_data(country="CZE", trace_id="trace-1", wrong=False):
    return {
        "experiences": [
            {
                "key": "station-country",
                "content": {
                    "situation": "Counting stations by country.",
                    "action": "Filter stations by country code.",
                    "outcome": "The imported query returned 12 for the recorded data.",
                    "lesson": "Query current data; a historical count is not a current answer.",
                },
            }
        ],
        "tools": [
            {
                "key": "count-country",
                "content": {
                    "name": "count_stations",
                    "description": "Count stations in a country using current data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"country": {"type": "string"}},
                        "required": ["country"],
                        "additionalProperties": False,
                    },
                    "output_schema": {"type": "object"},
                    "implementation": {
                        "kind": "parameterized_sql",
                        "sql": "SELECT COUNT(*) AS total FROM stations WHERE country != ?"
                        if wrong
                        else "SELECT COUNT(*) AS total FROM stations WHERE country = ?",
                        "parameter_order": ["country"],
                        "dialect": "sqlite",
                        "database_name": "cards",
                    },
                },
                "examples": [{"trace_id": trace_id, "call_id": "call-1", "arguments": {"country": country}}],
            }
        ],
        "skills": [
            {
                "key": "count-stations",
                "content": {
                    "name": "count-stations",
                    "description": "Count stations by country.",
                    "instructions": "Read the country code and call count_stations. Answer from its current result.",
                    "validation": ["Check that the tool result matches the requested country and use current data."],
                },
                "tool_keys": ["count-country"],
            }
        ],
    }


class Generator:
    config_id = "test-generator"

    def __init__(self, *, wrong=False):
        self.inputs = []
        self.wrong = wrong

    async def generate(self, value):
        from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
        from powercontext.builtin.trace_learning.models import GeneratedTraceLearningBundle

        self.inputs.append(value)
        trace = value.traces[0]
        country = "SVK" if "SVK" in trace.question else "CZE"
        return GenerationResult(
            output=GeneratedTraceLearningBundle.model_validate(bundle_data(country, trace.trace_id, self.wrong)),
            usage=InferenceUsage(requests=1, input_tokens=200, output_tokens=100),
        )


def test_candidate_workflow_blind_review_repairs_with_history_and_keeps_other_artifacts(tmp_path):
    async def scenario():
        from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
        from pydantic_ai.models.function import FunctionModel

        from powercontext.builtin.inference.pydantic_ai import InferenceLimits
        from powercontext.builtin.trace_learning.generation import CandidateLearningGenerator
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget

        reviewed = []
        repaired = []
        source = bundle_data()

        def respond(messages, info):
            prompt = next(
                part.content for msg in reversed(messages) for part in msg.parts if isinstance(part, UserPromptPart)
            )
            value = json.loads(prompt)
            if "tool" in value:
                assert set(value) == {"tool"}
                assert "trace-1" not in str(messages)
                assert "How many stations" not in str(messages)
                reviewed.append(value)
                findings = (
                    []
                    if value["tool"]["input_schema"]["properties"]["country"].get("description")
                    else [
                        {
                            "id": "input-meaning",
                            "category": "input_contract",
                            "comment": "Parameter meaning is unspecified.",
                            "suggestion": "Describe accepted values.",
                        }
                    ]
                )
                return ModelResponse(parts=[TextPart(json.dumps({"findings": findings}))])
            if value["phase"] == "discover":
                return ModelResponse(
                    parts=[
                        TextPart(
                            json.dumps({
                                "candidates": [
                                    {
                                        "family": "experience",
                                        "key": "station-country",
                                        "purpose": "Current counts",
                                        "trace_ids": ["trace-1"],
                                    },
                                    {
                                        "family": "tool",
                                        "key": "count-country",
                                        "purpose": "Count by country",
                                        "trace_ids": ["trace-1"],
                                    },
                                    {
                                        "family": "tool",
                                        "key": "bad-count",
                                        "purpose": "Broken candidate",
                                        "trace_ids": ["trace-1"],
                                    },
                                    {
                                        "family": "skill",
                                        "key": "count-stations",
                                        "purpose": "Counting workflow",
                                        "trace_ids": ["trace-1"],
                                        "tool_keys": ["count-country"],
                                    },
                                ]
                            })
                        )
                    ]
                )
            spec = value["candidate"]
            item = json.loads(
                json.dumps(source[{"experience": "experiences", "tool": "tools", "skill": "skills"}[spec["family"]]][0])
            )
            item["key"] = spec["key"]
            decisions = []
            if spec["key"] == "bad-count":
                item["content"]["implementation"]["sql"] = "SELECT COUNT(*) AS total FROM stations WHERE country != ?"
            if value.get("feedback") and spec["key"] == "count-country":
                assert "trace-1" in str(messages[:-1])
                repaired.append(value)
                item["content"]["input_schema"]["properties"]["country"]["description"] = (
                    "Country code stored in stations.country."
                )
                decisions = [
                    {
                        "finding_id": "input-meaning",
                        "decision": "accept",
                        "reason": "Makes the parameter usable without the trace.",
                    }
                ]
            return ModelResponse(parts=[TextPart(json.dumps({"candidate": item, "decisions": decisions}))])

        budget = LearningBudget(max_candidate_repair_rounds=1, max_model_calls=30)
        generator = CandidateLearningGenerator(
            model=FunctionModel(respond), limits=InferenceLimits(max_requests=2), config_id="candidate-test"
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = budget
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert {ref.family for ref in done.artifacts} == {"experience", "tool", "skill"}
            assert len(done.artifacts) == 3
            assert reviewed and repaired
            outcomes = {item.key: item for item in done.candidate_outcomes}
            assert outcomes["bad-count"].status == "rejected"
            assert outcomes["count-country"].status == "published"
            async with service.database.transaction() as connection:
                record = await service.repository.get(connection, "learning", run.run_id)
            candidate = next(item for item in record.candidates if item.spec.key == "count-country")
            assert candidate.messages and len(candidate.revisions) == 2
            assert candidate.revisions[-1].decisions[0].decision == "accept"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def learned_request(tool, key="learned", trace_id="trace-2"):
    value = request_data(key, trace_id, "SVK")
    result = {"columns": [{"name": "total"}], "rows": [{"total": 8}], "truncated": False}
    value["traces"][0]["tool_calls"] = [
        {
            "call_id": "call-1",
            "name": tool.content.name,
            "arguments": {"country": "SVK"},
            "result": {
                "tool_call_id": "call-1",
                "content": json.dumps(result),
                "status": "success",
                "artifact": {
                    "powercontext_tool": {
                        "ref": tool.as_ref().model_dump(mode="json"),
                        "name": tool.content.name,
                        "sql": tool.content.implementation.sql,
                        "database_name": "cards",
                        "arguments": {"country": "SVK"},
                        "complete": True,
                        "result": result,
                    }
                },
            },
        }
    ]
    value["traces"][0]["final_answer"] = "8 stations."
    return value


def test_learned_call_is_resolved_from_exact_stored_tool_and_preserved_for_future_learning(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        generator = Generator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", first.run_id)
            async with service.database.transaction() as connection:
                ref = next(ref for ref in done.artifacts if ref.family == "tool")
                tool = await service.contexts.repositories.artifacts.get(connection, "learning", ref)
            request = ImportTraceLearningRequest.model_validate(learned_request(tool))
            second = await service.import_traces("learning", "runtime", request)
            await invoke(service)
            learned = await service.get_run("learning", "runtime", second.run_id)
            assert learned.status == "succeeded", learned.error
            assert all(ref.revision == 2 for ref in learned.artifacts)
            assert learned.validation.checked_examples == 2
            value = generator.inputs[1]
            assert value.traces == request.traces
            assert value.traces[0].tool_calls[0].arguments == {"country": "SVK"}
            resolved = value.resolved_tool_calls[0]
            assert resolved.tool_ref == tool.as_ref()
            assert resolved.content == tool.content
            assert resolved.executed_sql == "SELECT COUNT(*) AS total FROM stations WHERE country = 'SVK'"
            async with service.database.transaction() as connection:
                source = await service.contexts.repositories.sources.get(connection, "learning", learned.sources[0])
            assert json.loads(source.value.content) == request.traces[0].model_dump(mode="json")

            # Read revision 1 after revision 2 exists, also preserving the second run's evidence.
            third = await service.import_traces(
                "learning",
                "runtime",
                ImportTraceLearningRequest.model_validate(learned_request(tool, "third", "trace-3")),
            )
            await invoke(service)
            evolved = await service.get_run("learning", "runtime", third.run_id)
            assert evolved.status == "succeeded", evolved.error
            assert all(ref.revision == 3 for ref in evolved.artifacts)
            assert evolved.validation.checked_examples == 3
            assert generator.inputs[2].resolved_tool_calls[0].tool_ref == tool.as_ref()
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_learned_receipt_requires_read_access_to_its_exact_historical_tool(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, TraceLearningError

        generator = Generator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            old_tool = None
            for key in ("first", "second"):
                accepted = await service.import_traces(
                    "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data(key, key))
                )
                await invoke(service)
                done = await service.get_run("learning", "runtime", accepted.run_id)
                assert done.status == "succeeded", done.error
                if key == "first":
                    async with service.database.transaction() as connection:
                        tool_ref = next(ref for ref in done.artifacts if ref.family == "tool")
                        old_tool = await service.contexts.repositories.artifacts.get(connection, "learning", tool_ref)
            assert old_tool is not None

            async def authorize(scope, principal, permission, ref):
                if permission == "read" and ref == old_tool.as_ref():
                    raise TraceLearningError("access_revoked")

            service.authorize = authorize
            learned = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(learned_request(old_tool))
            )
            await invoke(service)
            failed = await service.get_run("learning", "runtime", learned.run_id)
            assert failed.status == "failed" and failed.error == "access_revoked"
            assert failed.artifacts == () and len(generator.inputs) == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", ["sql", "arguments", "result"])
def test_forged_learned_receipt_does_not_become_training_evidence(tmp_path, tamper):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        generator = Generator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            good = await service.get_run("learning", "runtime", first.run_id)
            async with service.database.transaction() as connection:
                tool = next(a for a in await service.learned_artifacts(connection, "learning") if a.family == "tool")
            data = learned_request(tool)
            receipt = data["traces"][0]["tool_calls"][0]["result"]["artifact"]["powercontext_tool"]
            if tamper == "sql":
                receipt["sql"] = "SELECT 8"
            elif tamper == "arguments":
                receipt["arguments"] = {"country": "CZE"}
            else:
                receipt["result"]["rows"] = [{"total": 999}]
            second = await service.import_traces("learning", "runtime", ImportTraceLearningRequest.model_validate(data))
            await invoke(service)
            failed = await service.get_run("learning", "runtime", second.run_id)
            assert failed.status == "failed" and failed.error == "invalid_learned_tool_receipt"
            assert failed.artifacts == () and len(generator.inputs) == 1
            async with service.database.transaction() as connection:
                assert {
                    a.as_ref().model_dump_json() for a in await service.learned_artifacts(connection, "learning")
                } == {ref.model_dump_json() for ref in good.artifacts}
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_source_call_arguments_cannot_be_used_as_generated_tool_example(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import GeneratedTraceLearningBundle, ImportTraceLearningRequest

        class SourceArgumentGenerator(Generator):
            async def generate(self, value):
                result = await super().generate(value)
                data = result.output.model_dump(mode="json")
                data["tools"][0]["examples"][0]["arguments"] = value.traces[0].tool_calls[0].arguments
                return result.model_copy(update={"output": GeneratedTraceLearningBundle.model_validate(data)})

        generator = SourceArgumentGenerator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            accepted = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            run = await service.get_run("learning", "runtime", accepted.run_id)
            assert run.status == "failed" and run.error == "invalid_tool_example_arguments"
            assert run.artifacts == ()
            assert run.usage.model_calls == len(generator.inputs) == 1
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_generated_skill_name_matches_the_canonical_package_contract():
    from pydantic import ValidationError

    from powercontext.builtin.artifacts.skill import SkillContent, build_instruction_skill_package
    from powercontext.builtin.trace_learning.models import GeneratedTraceLearningBundle

    data = bundle_data()
    data["skills"][0]["content"]["name"] = "Station Count Analysis"
    # Legacy Artifact content is still accepted; generated Skills must already be package compatible.
    assert SkillContent.model_validate(data["skills"][0]["content"]).name == "Station Count Analysis"
    with pytest.raises(ValidationError, match=r"skills\.0\.content\.name"):
        GeneratedTraceLearningBundle.model_validate(data)
    data["skills"][0]["content"]["name"] = "station-count-analysis"
    generated = GeneratedTraceLearningBundle.model_validate(data)
    assert (
        build_instruction_skill_package(generated.skills[0].content).as_skill_content().name == "station-count-analysis"
    )


def test_invalid_skill_package_is_diagnosed_before_any_artifact_is_saved(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        class InvalidPackageGenerator(Generator):
            async def generate(self, value):
                result = await super().generate(value)
                # Valid text fields can still exceed the canonical package's encoded byte limit.
                result.output.skills[0].content = result.output.skills[0].content.model_copy(
                    update={"instructions": "规则" * 40_000}
                )
                return result

        manager, service = await setup_service(tmp_path, InvalidPackageGenerator())
        try:
            accepted = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            failed = await service.get_run("learning", "runtime", accepted.run_id)
            assert failed.status == "failed" and failed.error == "invalid_skill_package"
            assert failed.artifacts == ()
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


async def invoke(service):
    from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
    from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
    from powercontext.builtin.runtime.processing_execution import ScopeInvocation
    from powercontext.builtin.trace_learning.models import TRACE_LEARNING_BINDING

    async with service.database.transaction() as connection:
        term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "test-worker")
        intent = await service.intents.load(connection, "learning", TRACE_LEARNING_BINDING)
    assert intent is not None
    return await service.execute(
        ScopeInvocation(
            ArtifactProcessingWorkAssignment(
                binding_name=TRACE_LEARNING_BINDING,
                artifact_family="tool",
                scope_id="learning",
                claimed_request_generation=intent.requested_generation,
                fence=term.fence("single-process"),
                worker_id="test-worker",
            )
        )
    )


async def setup_service(tmp_path, generator):
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime.composition import open_builtin_contexts
    from powercontext.builtin.runtime.config import BuiltinConfig
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.builtin.scope.application import ScopeApplication
    from powercontext.builtin.trace_learning.service import TraceLearningService

    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'learning.db'}"))
    manager = open_builtin_contexts(config)
    contexts = await manager.__aenter__()
    await ScopeApplication(contexts.database, id_factory=lambda: "learning").create(
        ScopeDraft(title="Learning", summary="Trace learning tests", idempotency_key="learning-scope")
    )
    from powercontext.builtin.trace_learning.models import LearningBudget

    return manager, TraceLearningService(
        contexts=contexts, generator=generator, budget=LearningBudget(max_model_calls=3)
    )


def test_import_preserves_complete_trace_and_replay_is_idempotent(tmp_path):
    async def scenario():
        from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
        from powercontext.builtin.trace_learning.models import (
            TRACE_LEARNING_BINDING,
            ImportTraceLearningRequest,
            TraceLearningError,
        )

        manager, service = await setup_service(tmp_path, Generator())
        try:
            ordinary = await CONTENT_SOURCE_ADAPTER.resolve(
                ContentCapture(source_id="ordinary", content="ordinary log")
            )
            async with service.database.transaction() as connection:
                await service.contexts.repositories.sources.add(connection, "learning", ordinary)
                assert await service.intents.load(connection, "learning", TRACE_LEARNING_BINDING) is None
            request = ImportTraceLearningRequest.model_validate(request_data())
            run = await service.import_traces("learning", "runtime", request)
            assert run.status == "queued"
            assert await service.import_traces("learning", "runtime", request) == run
            async with service.database.transaction() as connection:
                stored = await service.contexts.repositories.sources.get(connection, "learning", run.sources[0])
            assert '"rows":[[12]]' in stored.value.content
            assert '"call_id":"call-1"' in stored.value.content
            with pytest.raises(TraceLearningError, match="idempotency_conflict"):
                await service.import_traces("learning", "runtime", request.model_copy(update={"traces": ()}))
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_learning_saves_exact_tool_dependencies_and_revises_previous_bundle(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        generator = Generator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", first.run_id)
            assert done.status == "succeeded", done.error
            assert done.validation.covered_path_verified and not done.validation.live_execution_verified
            assert done.usage.model_calls == 1
            assert {ref.family for ref in done.artifacts} == {"experience", "skill", "tool"}
            async with service.database.transaction() as connection:
                artifacts = await service.learned_artifacts(connection, "learning")
                skill = next(a for a in artifacts if a.family == "skill")
                tool = next(a for a in artifacts if a.family == "tool")
                assert skill.content.tool_dependencies == (tool.as_ref(),)
                package = await service.contexts.repositories.skill_packages.get(
                    connection, "learning", skill.content.package
                )
                assert package.as_skill_content().tool_dependencies == (tool.as_ref(),)
            second = await service.import_traces(
                "learning",
                "runtime",
                ImportTraceLearningRequest.model_validate(request_data("second", "trace-2", "SVK")),
            )
            await invoke(service)
            updated = await service.get_run("learning", "runtime", second.run_id)
            assert updated.status == "succeeded", updated.error
            assert all(ref.revision == 2 for ref in updated.artifacts)
            assert {ref.artifact_id for ref in done.artifacts} == {ref.artifact_id for ref in updated.artifacts}
            assert {item.ref.family for item in generator.inputs[1].previous_artifacts} == {
                "experience",
                "skill",
                "tool",
            }
            async with service.database.transaction() as connection:
                assert len(await service.learned_artifacts(connection, "learning")) == 3
            assert generator.inputs[0].traces == (ImportTraceLearningRequest.model_validate(request_data()).traces[0],)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_active_learning_revision_survives_out_of_order_run_completion(tmp_path):
    async def scenario():
        from datetime import UTC, datetime

        from sqlalchemy import update

        from powercontext.builtin.persistence.dream import ticks
        from powercontext.builtin.persistence.tables import TRACE_LEARNING_RUNS_TABLE
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        manager, service = await setup_service(tmp_path, Generator())
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            second = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data("second"))
            )
            await invoke(service)
            latest = await service.get_run("learning", "runtime", second.run_id)
            # Persist the ordering of an earlier accepted run that finishes last.
            # Acceptance order must not decide which published revision is visible.
            async with service.database.transaction() as connection:
                for run_id, year in ((first.run_id, 2021), (second.run_id, 2020)):
                    await connection.execute(
                        update(TRACE_LEARNING_RUNS_TABLE)
                        .where(TRACE_LEARNING_RUNS_TABLE.c.run_id == run_id)
                        .values(accepted_at=ticks(datetime(year, 1, 1, tzinfo=UTC)))
                    )
                recalled = await service.learned_artifacts(connection, "learning")
            assert {artifact.as_ref().model_dump_json() for artifact in recalled} == {
                ref.model_dump_json() for ref in latest.artifacts
            }
            assert {artifact.family for artifact in recalled} == {"experience", "skill", "tool"}
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_changed_sql_cannot_pass_using_recorded_result(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        generator = Generator(wrong=True)
        manager, service = await setup_service(tmp_path, generator)
        try:
            accepted = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            run = await service.get_run("learning", "runtime", accepted.run_id)
            assert run.status == "failed" and run.error == "trace_sql_mismatch"
            assert run.artifacts == ()
            assert run.usage.model_calls == len(generator.inputs) == run.budget.max_model_calls
            async with service.database.transaction() as connection:
                assert await service.learned_artifacts(connection, "learning") == ()
                record = await service.repository.get(connection, "learning", run.run_id)
                assert len(record.rejected_candidates) == run.budget.max_model_calls
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_sql_alias_feedback_repairs_candidate_before_any_artifact_is_published(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import (
            GeneratedTraceLearningBundle,
            ImportTraceLearningRequest,
            LearningBudget,
        )

        class RepairGenerator(Generator):
            async def generate(self, value):
                result = await super().generate(value)
                data = result.output.model_dump(mode="json")
                if len(self.inputs) == 1:
                    data["tools"][0]["content"]["implementation"]["sql"] = (
                        "SELECT COUNT(*) AS generic_count FROM stations WHERE country = ?"
                    )
                    data["tools"][0]["examples"].append({
                        "trace_id": "trace-2",
                        "call_id": "call-1",
                        "arguments": {"country": "SVK"},
                    })
                else:
                    rejected = value.validation_feedback
                    assert rejected is not None
                    assert len(rejected.feedback) == 2
                    feedback = rejected.feedback[0]
                    assert feedback.code == "trace_sql_mismatch"
                    assert (feedback.tool_key, feedback.trace_id, feedback.call_id, feedback.query_index) == (
                        "count-country",
                        "trace-1",
                        "call-1",
                        0,
                    )
                    assert feedback.actual_sql == (
                        "SELECT COUNT(*) AS generic_count FROM stations WHERE country = 'CZE'"
                    )
                    assert rejected.feedback[1].expected_sql == (
                        "SELECT COUNT(*) AS other_total FROM stations WHERE country = 'SVK'"
                    )
                    data["tools"][0]["content"]["implementation"]["sql"] = feedback.expected_sql.replace("'CZE'", "?")
                    async with service.database.transaction() as connection:
                        assert await service.learned_artifacts(connection, "learning") == ()
                        record = await service.repository.get(connection, "learning", accepted.run_id)
                        assert record.run.usage.model_calls == 2
                        assert len(record.rejected_candidates) == 1
                return result.model_copy(
                    update={
                        "output": GeneratedTraceLearningBundle.model_validate(data),
                        "usage": result.usage.model_copy(update={"output_tokens": 800}),
                    }
                )

        generator = RepairGenerator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            service.budget = LearningBudget(max_output_tokens=1024)
            data = request_data()
            other_trace = request_data("other", "trace-2", "SVK")["traces"][0]
            other_trace["tool_calls"][0]["arguments"]["sql"] = (
                "SELECT COUNT(*) AS other_total FROM stations WHERE country = 'SVK'"
            )
            data["traces"].append(other_trace)
            accepted = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(data)
            )
            await invoke(service)
            run = await service.get_run("learning", "runtime", accepted.run_id)
            assert run.status == "succeeded", run.error
            assert run.usage.model_calls == len(generator.inputs) == 2
            assert (run.usage.input_tokens, run.usage.output_tokens) == (400, 1600)
            assert run.validation is not None and run.validation.checked_examples == 1
            async with service.database.transaction() as connection:
                record = await service.repository.get(connection, "learning", run.run_id)
                rejected = record.rejected_candidates[0]
                assert "generic_count" in rejected.candidate.tools[0].content.implementation.sql
                tool = next(a for a in await service.learned_artifacts(connection, "learning") if a.family == "tool")
                assert "generic_count" not in tool.content.implementation.sql
            assert generator.inputs[0].traces == generator.inputs[1].traces
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_sql_repair_recovery_keeps_dispatched_call_budget_and_deadline(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest
        from powercontext.builtin.trace_learning.service import TraceLearningService

        class InterruptedRepair(Generator):
            async def generate(self, value):
                result = await super().generate(value)
                if len(self.inputs) == 2:
                    raise asyncio.CancelledError
                return result

        generator = InterruptedRepair(wrong=True)
        manager, service = await setup_service(tmp_path, generator)
        try:
            accepted = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            with pytest.raises(asyncio.CancelledError):
                await invoke(service)
            async with service.database.transaction() as connection:
                interrupted = await service.repository.get(connection, "learning", accepted.run_id)
                assert interrupted.run.usage.model_calls == 2
                assert len(interrupted.rejected_candidates) == 1
            recovered_service = TraceLearningService(contexts=service.contexts, generator=generator)
            await invoke(recovered_service)
            run = await recovered_service.get_run("learning", "runtime", accepted.run_id)
            assert run.status == "failed" and run.error == "trace_sql_mismatch"
            assert run.usage.model_calls == len(generator.inputs) == 3
            assert run.artifacts == ()
            async with service.database.transaction() as connection:
                recovered = await service.repository.get(connection, "learning", accepted.run_id)
                assert recovered.deadline_at == interrupted.deadline_at
                assert len(recovered.rejected_candidates) == 2
                assert await service.learned_artifacts(connection, "learning") == ()
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_sql_repair_feedback_obeys_input_size_budget(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import (
            ImportTraceLearningRequest,
            LearningBudget,
            TraceLearningGenerationInput,
        )

        generator = Generator(wrong=True)
        manager, service = await setup_service(tmp_path, generator)
        try:
            request = ImportTraceLearningRequest.model_validate(request_data())
            original_input = TraceLearningGenerationInput(traces=request.traces, host_profile=request.host_profile)
            service.budget = LearningBudget(max_input_chars=max(1024, len(original_input.model_dump_json()) + 100))
            accepted = await service.import_traces("learning", "runtime", request)
            await invoke(service)
            run = await service.get_run("learning", "runtime", accepted.run_id)
            assert run.status == "failed" and run.error == "input_budget_exceeded"
            assert run.usage.model_calls == len(generator.inputs) == 1
            assert run.artifacts == ()
            async with service.database.transaction() as connection:
                record = await service.repository.get(connection, "learning", accepted.run_id)
                assert len(record.rejected_candidates) == 1
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_recovery_uses_checkpointed_generation_without_another_model_call(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest
        from powercontext.builtin.trace_learning.service import TraceLearningService

        class InterruptedSave(TraceLearningService):
            async def _save_bundle(self, connection, record):
                raise asyncio.CancelledError

        generator = Generator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            interrupted = InterruptedSave(contexts=service.contexts, generator=generator)
            run = await interrupted.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            with pytest.raises(asyncio.CancelledError):
                await invoke(interrupted)
            assert (await service.get_run("learning", "runtime", run.run_id)).status == "running"
            await invoke(service)
            recovered = await service.get_run("learning", "runtime", run.run_id)
            assert recovered.status == "succeeded", recovered.error
            assert len(generator.inputs) == 1 and recovered.usage.model_calls == 1
            assert len(recovered.artifacts) == 3
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_user_import_arriving_during_learning_is_not_lost(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        class BlockedGenerator(Generator):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def generate(self, value):
                self.started.set()
                await self.release.wait()
                return await super().generate(value)

        generator = BlockedGenerator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            work = asyncio.create_task(invoke(service))
            await asyncio.wait_for(generator.started.wait(), timeout=5)
            second = await service.import_traces(
                "learning",
                "runtime",
                ImportTraceLearningRequest.model_validate(request_data("second", "trace-2", "SVK")),
            )
            generator.release.set()
            await work
            assert (await service.get_run("learning", "runtime", first.run_id)).status == "succeeded"
            assert (await service.get_run("learning", "runtime", second.run_id)).status == "queued"
            await invoke(service)
            assert (await service.get_run("learning", "runtime", second.run_id)).status == "succeeded"
            assert [value.traces[0].trace_id for value in generator.inputs] == ["trace-1", "trace-2"]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_failed_new_import_keeps_previous_successful_artifacts(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest

        generator = Generator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            good = await service.get_run("learning", "runtime", first.run_id)
            generator.wrong = True
            second = await service.import_traces(
                "learning",
                "runtime",
                ImportTraceLearningRequest.model_validate(request_data("second", "trace-2", "SVK")),
            )
            await invoke(service)
            assert (await service.get_run("learning", "runtime", second.run_id)).status == "failed"
            async with service.database.transaction() as connection:
                artifacts = await service.learned_artifacts(connection, "learning")
            assert {value.as_ref().model_dump_json() for value in artifacts} == {
                ref.model_dump_json() for ref in good.artifacts
            }
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_structured_model_generation_uses_dedicated_prompt_and_counts_usage():
    async def scenario():
        import json

        from pydantic_ai.models.test import TestModel

        from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
        from powercontext.builtin.trace_learning.generation import LLMTraceLearningGenerator
        from powercontext.builtin.trace_learning.models import (
            GeneratedTraceLearningBundle,
            ImportTraceLearningRequest,
            TraceLearningGenerationInput,
        )
        from powercontext.builtin.trace_learning.prompts import TRACE_LEARNING_INSTRUCTIONS

        generator = LLMTraceLearningGenerator(
            PydanticAIStructuredGenerator(
                model=TestModel(custom_output_text=json.dumps(bundle_data())),
                instructions=TRACE_LEARNING_INSTRUCTIONS,
                input_type=TraceLearningGenerationInput,
                output_type=GeneratedTraceLearningBundle,
                limits=InferenceLimits(max_requests=1),
            ),
            config_id="structured-test",
        )
        request = ImportTraceLearningRequest.model_validate(request_data())
        result = await generator.generate(
            TraceLearningGenerationInput(traces=request.traces, host_profile=request.host_profile)
        )
        assert result.usage.requests == 1
        assert result.output.tools[0].examples[0].call_id == "call-1"

    asyncio.run(scenario())


def test_datus_multiple_queries_validate_selected_successful_query():
    from powercontext.builtin.trace_learning.models import (
        GeneratedTraceLearningBundle,
        ImportTraceLearningRequest,
        TraceLearningError,
    )
    from powercontext.builtin.trace_learning.service import validate_bundle

    request = request_data()
    call = request["traces"][0]["tool_calls"][0]
    sql = call["arguments"]["sql"]
    call["name"] = "read_queries"
    call["arguments"] = {"queries": ["SELECT 1", sql]}
    call["result"] = {
        "status": "success",
        "artifact": {
            "queries": [
                {"sql": "SELECT 1", "rows": [[1]], "columns": ["1"], "truncated": False},
                {"sql": sql, "rows": [[12]], "columns": ["total"], "truncated": False},
            ]
        },
    }
    data = bundle_data()
    data["tools"][0]["examples"][0]["query_index"] = 1
    imported = ImportTraceLearningRequest.model_validate(request)
    bundle = GeneratedTraceLearningBundle.model_validate(data)
    assert validate_bundle(bundle, imported.traces, imported.host_profile).covered_path_verified
    call["result"]["artifact"]["queries"][1]["truncated"] = True
    truncated = ImportTraceLearningRequest.model_validate(request)
    with pytest.raises(TraceLearningError, match="invalid_trace_example"):
        validate_bundle(bundle, truncated.traces, truncated.host_profile)


@pytest.mark.parametrize("with_embeddings", [False, True])
def test_real_supervisor_spawn_worker_learns_through_model_http(tmp_path, monkeypatch, with_embeddings):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    monkeypatch.setenv("OPENAI_API_KEY", "trace-learning-local-test")
    requests = []

    class ModelHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path.endswith("/embeddings"):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({
                    "object": "list",
                    "model": "test-embedding",
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [1.0, 0.0, 0.0]}
                        for index, _ in enumerate(request["input"])
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            value = json.loads(
                next(message["content"] for message in reversed(request["messages"]) if message["role"] == "user")
            )
            bundle = bundle_data()
            if value.get("phase") == "discover":
                output = {
                    "candidates": [
                        {
                            "family": family,
                            "key": item["key"],
                            "purpose": "Reusable station capability",
                            "trace_ids": ["trace-1"],
                            "tool_keys": item.get("tool_keys", []),
                        }
                        for family, group in (("experience", "experiences"), ("tool", "tools"), ("skill", "skills"))
                        for item in bundle[group]
                    ]
                }
            elif "tool" in value:
                output = {"findings": []}
            else:
                family = value["candidate"]["family"]
                group = {"experience": "experiences", "tool": "tools", "skill": "skills"}[family]
                output = {"candidate": bundle[group][0], "decisions": []}
            body = json.dumps({
                "id": "trace-learning-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib override
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime.composition import open_builtin_runtime
        from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
        from powercontext.builtin.scope import ScopeDraft
        from powercontext.builtin.trace_learning.models import GetLearningRunRequest, ImportTraceLearningRequest

        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn.db'}"),
            inference=InferenceConfig.model_validate({
                "generation_model": "openai-chat:test-model",
                "generation_base_url": f"http://127.0.0.1:{server.server_port}/v1",
                **(
                    {
                        "embedding_model": "openai:test-embedding",
                        "embedding_base_url": f"http://127.0.0.1:{server.server_port}/v1",
                        "embedding_profile_id": "trace-learning-worker-test",
                        "embedding_dimension": 3,
                    }
                    if with_embeddings
                    else {}
                ),
            }),
            runtime=RuntimeConfig(
                artifact_processing_families=("tool",), trace_learning_enabled=True, tool_worker_timeout_seconds=30
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Spawn learning", summary="Worker test", idempotency_key="scope")
            )
            app = runtime.trace_learning.for_scope(scope.scope_id)
            accepted = await app.import_traces(ImportTraceLearningRequest.model_validate(request_data()))
            async with asyncio.timeout(25):
                while True:
                    run = await app.get(GetLearningRunRequest(run_id=accepted.run_id))
                    if run.terminal:
                        break
                    await asyncio.sleep(0.05)
            assert run.status == "succeeded", run.error
            assert run.usage.model_calls == len(requests) and len(run.artifacts) == 3
            assert len(requests) >= 5
            assert "trace-1" in json.dumps(requests[0])
            assert any("Historical data" in json.dumps(request) for request in requests)
            from powercontext.builtin.runtime.models import PrepareContextRequest

            prepared = await runtime.context.for_scope(scope.scope_id).prepare(
                PrepareContextRequest.model_validate({
                    "query": "How many stations in SVK?",
                    "assembly": {"sections": []},
                    "learned_tools": True,
                    "host_profile": {"kind": "datus", "dialect": "sqlite", "database_name": "cards"},
                })
            )
            assert prepared.status == "ready"
            learned = prepared.learned_context
            assert learned and learned.experiences and learned.skills and learned.tools
            assert learned.skills[0].tool_dependencies == (learned.tools[0].ref,)
            assert prepared.content is None and prepared.content_bytes == 0
            assert len(learned.model_dump_json().encode()) <= 8000

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def candidate_generator_for(specs, *, findings=(), reject_review=False):
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
            output = {"findings": list(findings)}
        elif value["phase"] == "discover":
            output = {"candidates": specs}
        else:
            family = value["candidate"]["family"]
            item = bundle_data()[{"experience": "experiences", "tool": "tools", "skill": "skills"}[family]][0]
            item["key"] = value["candidate"]["key"]
            decisions = []
            if value.get("feedback") and reject_review:
                decisions = [
                    {
                        "finding_id": finding["id"],
                        "decision": "reject",
                        "reason": "Table identifiers are intentionally fixed; only SQL literal inputs are variable.",
                    }
                    for finding in findings
                ]
            output = {"candidate": item, "decisions": decisions}
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    generator = CandidateLearningGenerator(
        model=FunctionModel(respond), limits=InferenceLimits(max_requests=2), config_id="candidate-tests"
    )
    return generator, seen


def candidate_spec(family, key):
    return {"family": family, "key": key, "purpose": "Reusable station method", "trace_ids": ["trace-1"]}


def test_generator_can_reject_advisory_findings_with_reasons(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget

        findings = [
            {
                "id": "fixed-table",
                "category": "parameterization",
                "comment": "Table name is fixed.",
                "suggestion": "Consider a table parameter.",
            }
        ]
        generator, seen = candidate_generator_for(
            [candidate_spec("tool", "count-country")], findings=findings, reject_review=True
        )
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=8)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert done.candidate_outcomes[0].repair_rounds == 1
            assert done.candidate_outcomes[0].review_rounds == 1
            async with service.database.transaction() as connection:
                record = await service.repository.get(connection, "learning", run.run_id)
            assert record.candidates[0].revisions[-1].decisions[0].decision == "reject"
            assert len(record.run.artifacts) == 1
            assert done.usage.model_calls == len(seen)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_candidate_caps_and_total_budget_preserve_completed_experience(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget

        specs = [
            candidate_spec("experience", "station-country"),
            candidate_spec("experience", "extra"),
            candidate_spec("tool", "count-country"),
        ]
        generator, seen = candidate_generator_for(specs)
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_candidates_per_family=1, max_model_calls=3)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert [ref.family for ref in done.artifacts] == ["experience"]
            outcomes = {item.key: item for item in done.candidate_outcomes}
            assert outcomes["extra"].reason == "candidate_limit"
            assert outcomes["count-country"].reason == "budget_exceeded"
            assert done.usage.model_calls == len(seen) == 3
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_candidate_worker_restart_keeps_published_artifacts_and_generator_messages(tmp_path):
    async def scenario():
        from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget

        specs = [candidate_spec("experience", "station-country"), candidate_spec("tool", "count-country")]
        generator, seen = candidate_generator_for(specs)
        review = generator.review_tool
        interrupted = False

        async def interrupt_review(tool, *, max_requests):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise InvocationAlreadyHandled()
            return await review(tool, max_requests=max_requests)

        generator.review_tool = interrupt_review
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=12)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            with pytest.raises(InvocationAlreadyHandled):
                await invoke(service)
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert all(ref.revision == 1 for ref in done.artifacts)
            generated = [value["candidate"]["key"] for value in seen if value.get("phase") == "generate"]
            assert generated == ["station-country", "count-country"]
            assert len([value for value in seen if value.get("phase") == "discover"]) == 1
            assert done.usage.reserved_model_calls == generator.max_requests
            assert done.usage.model_calls - done.usage.reserved_model_calls == len(seen)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_restart_after_review_cannot_publish_before_generator_addresses_findings(tmp_path, monkeypatch):
    async def scenario():
        from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget
        from powercontext.builtin.trace_learning.workflow import CandidateWorkflow

        generator, _ = candidate_generator_for(
            [candidate_spec("tool", "count-country")],
            findings=[
                {
                    "id": "fixed-table",
                    "category": "parameterization",
                    "comment": "Table is fixed.",
                    "suggestion": "Consider making it variable.",
                }
            ],
            reject_review=True,
        )
        review = CandidateWorkflow._review
        first = True

        async def interrupt_after_review(self, index, item):
            nonlocal first
            await review(self, index, item)
            if first:
                first = False
                raise InvocationAlreadyHandled()

        monkeypatch.setattr(CandidateWorkflow, "_review", interrupt_after_review)
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=12)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            with pytest.raises(InvocationAlreadyHandled):
                await invoke(service)
            await invoke(service)
            async with service.database.transaction() as connection:
                record = await service.repository.get(connection, "learning", run.run_id)
            assert record.run.status == "succeeded", record.run.error
            assert len(record.candidates[0].revisions) == 2
            assert record.candidates[0].revisions[-1].decisions[0].decision == "reject"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_deadline_preserves_published_candidates_and_marks_unfinished_candidates(tmp_path):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget

        generator, _ = candidate_generator_for([
            candidate_spec("experience", "station-country"),
            candidate_spec("tool", "count-country"),
        ])
        generate = generator.generate_candidate

        async def timeout_tool(value, *, messages, max_requests):
            if value.candidate.family == "tool":
                raise TimeoutError
            return await generate(value, messages=messages, max_requests=max_requests)

        generator.generate_candidate = timeout_tool
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=12)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert done.error == "budget_exceeded"
            assert done.candidate_outcomes[1].status == "deferred"
            async with service.database.transaction() as connection:
                artifacts = await service.learned_artifacts(connection, "learning")
            assert [item.family for item in artifacts] == ["experience"]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_recall_and_revision_lookup_include_artifacts_older_than_thirty_runs(tmp_path):
    async def scenario():
        from powercontext.builtin.inference.models import GenerationResult
        from powercontext.builtin.runtime.learned_context import prepare_learned_context
        from powercontext.builtin.trace_learning.models import GeneratedTraceLearningBundle, ImportTraceLearningRequest

        class DistinctGenerator(Generator):
            async def generate(self, value):
                result = await super().generate(value)
                data = result.output.model_dump(mode="json")
                identity = value.traces[0].trace_id
                for group in ("experiences", "tools", "skills"):
                    data[group][0]["key"] += "-" + identity
                data["tools"][0]["content"]["name"] += "_" + identity
                data["skills"][0]["tool_keys"] = [data["tools"][0]["key"]]
                if identity == "first":
                    data["tools"][0]["content"]["description"] = "Unique earliest capability for attendance inventory."
                return GenerationResult(output=GeneratedTraceLearningBundle.model_validate(data), usage=result.usage)

        generator = DistinctGenerator()
        manager, service = await setup_service(tmp_path, generator)
        try:
            first = None
            for index in range(32):
                identity = "first" if index == 0 else f"later{index}"
                run = await service.import_traces(
                    "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data(identity, identity))
                )
                await invoke(service)
                done = await service.get_run("learning", "runtime", run.run_id)
                assert done.status == "succeeded", done.error
                if first is None:
                    first = next(ref for ref in done.artifacts if ref.family == "tool")
            async with service.database.transaction() as connection:
                artifacts = await service.learned_artifacts(connection, "learning")
            assert len(artifacts) == 96
            context = prepare_learned_context(
                artifacts,
                query="Unique earliest attendance inventory",
                dialect="sqlite",
                database_name="cards",
                max_bytes=8000,
            )
            assert context.tools[0].ref == first
            revised = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data("revise-first", "first"))
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", revised.run_id)
            assert done.status == "succeeded", done.error
            tool = next(ref for ref in done.artifacts if ref.family == "tool")
            assert first is not None
            assert tool.artifact_id == first.artifact_id and tool.revision == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_candidate_revision_conflict_does_not_stop_independent_candidates(tmp_path, monkeypatch):
    async def scenario():
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget
        from powercontext.errors import RevisionConflictError

        generator, _ = candidate_generator_for([
            candidate_spec("experience", "conflict"),
            candidate_spec("experience", "independent"),
        ])
        manager, service = await setup_service(tmp_path, generator)
        service.budget = LearningBudget(max_model_calls=10)
        save = service._save_bundle

        async def conflict_once(connection, record):
            if record.generated.experiences[0].key == "conflict":
                raise RevisionConflictError("conflict", "current")
            return await save(connection, record)

        monkeypatch.setattr(service, "_save_bundle", conflict_once)
        try:
            run = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            done = await service.get_run("learning", "runtime", run.run_id)
            assert done.status == "succeeded", done.error
            assert len(done.artifacts) == 1
            assert done.candidate_outcomes[0].reason == "artifact_revision_conflict"
            assert done.candidate_outcomes[1].status == "published"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_published_tool_revision_is_recallable_before_other_candidates_finish(tmp_path):
    async def scenario():
        from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled
        from powercontext.builtin.trace_learning.models import ImportTraceLearningRequest, LearningBudget

        manager, service = await setup_service(tmp_path, Generator())
        try:
            first = await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data())
            )
            await invoke(service)
            old = await service.get_run("learning", "runtime", first.run_id)
            old_tool = next(ref for ref in old.artifacts if ref.family == "tool")
            skill_spec = candidate_spec("skill", "count-stations") | {"tool_keys": ["count-country"]}
            generator, _ = candidate_generator_for([candidate_spec("tool", "count-country"), skill_spec])
            generate = generator.generate_candidate

            async def interrupt_skill(value, *, messages, max_requests):
                if value.candidate.family == "skill":
                    raise InvocationAlreadyHandled()
                return await generate(value, messages=messages, max_requests=max_requests)

            generator.generate_candidate = interrupt_skill
            service.generator = generator
            service.budget = LearningBudget(max_model_calls=12)
            await service.import_traces(
                "learning", "runtime", ImportTraceLearningRequest.model_validate(request_data("second"))
            )
            with pytest.raises(InvocationAlreadyHandled):
                await invoke(service)
            async with service.database.transaction() as connection:
                recalled = await service.learned_artifacts(connection, "learning")
            refs = [item.as_ref() for item in recalled if item.family == "tool"]
            assert len(refs) == 1 and refs[0].artifact_id == old_tool.artifact_id and refs[0].revision == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
