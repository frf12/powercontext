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
    return manager, TraceLearningService(contexts=contexts, generator=generator)


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
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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
                            "content": json.dumps(bundle_data()),
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
            assert run.usage.model_calls == 1 and len(run.artifacts) == 3
            assert len(requests) == 1
            assert "trace-1" in json.dumps(requests[0])
            assert "Historical data" in json.dumps(requests[0])
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
