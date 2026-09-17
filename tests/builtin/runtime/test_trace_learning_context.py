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

"""Opt-in learned context preserves the ordinary preparation contract."""

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime import PrepareContextRequest, RuntimeConfig


def _learned_artifacts():
    from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
    from powercontext.builtin.artifacts.skill import Skill, SkillContent
    from powercontext.builtin.artifacts.tool import SqlToolImplementation, Tool, ToolContent

    tool = Tool(
        artifact_id="station-count",
        revision=2,
        content=ToolContent(
            name="count_stations",
            description="Count gas stations by country",
            input_schema={"type": "object", "properties": {"country": {"type": "string"}}, "required": ["country"]},
            output_schema={"type": "object"},
            implementation=SqlToolImplementation(
                sql="SELECT COUNT(*) AS count FROM stations WHERE country = ?",
                parameter_order=("country",),
                dialect="mysql",
                database_name="debit_card",
            ),
        ),
    )
    skill = Skill(
        artifact_id="station-skill",
        revision=1,
        content=SkillContent(
            name="station-count",
            description="Count gas stations for a country",
            instructions="Use count_stations with the requested country code; report the current result.",
            validation=("Confirm the country code.",),
            tool_dependencies=(tool.as_ref(),),
        ),
    )
    experience = Experience(
        artifact_id="station-experience",
        revision=1,
        content=ExperienceContent(
            situation="Counting gas stations by country",
            action="Use the country code.",
            outcome="The historical query returned 100.",
            lesson="Country names use ISO country codes.",
        ),
    )
    return experience, skill, tool


def test_prepare_accepts_an_explicit_host_for_learned_tools() -> None:
    request = PrepareContextRequest.model_validate({
        "query": "count premium stations in Czech Republic",
        "learned_tools": True,
        "host_profile": {"kind": "datus", "dialect": "mysql", "database_name": "debit_card"},
    })
    assert request.learned_tools is True
    assert request.host_profile is not None
    assert request.host_profile.dialect == "mysql"


def test_learned_tools_require_a_host_profile() -> None:
    with pytest.raises(ValidationError, match="host_profile"):
        PrepareContextRequest.model_validate({"query": "stations", "learned_tools": True})


def test_trace_learning_is_explicitly_enabled_with_independent_worker_budget() -> None:
    config = RuntimeConfig(trace_learning_enabled=True, tool_max_workers=2, tool_worker_timeout_seconds=180)
    assert config.trace_learning_enabled is True
    assert config.tool_max_workers == 2
    assert config.skill_max_workers == 1


def test_trace_learning_remains_disabled_by_default() -> None:
    assert RuntimeConfig().trace_learning_enabled is False


def test_prepares_experience_skill_and_exact_callable_dependency_together() -> None:
    from powercontext.builtin.runtime.learned_context import prepare_learned_context

    experience, skill, tool = _learned_artifacts()
    context = prepare_learned_context(
        (experience, skill, tool),
        query="count gas stations by country",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
    )
    assert context.skills[0].ref == skill.as_ref()
    assert context.tools[0].ref == tool.as_ref()
    assert context.skills[0].tool_dependencies == (tool.as_ref(),)
    assert "ISO country codes" in context.experiences[0].text
    assert "100" not in context.experiences[0].text


def test_does_not_offer_a_skill_with_a_missing_tool_revision() -> None:
    from powercontext.builtin.runtime.learned_context import prepare_learned_context

    experience, skill, tool = _learned_artifacts()
    stale_tool = tool.model_copy(update={"revision": 1})
    context = prepare_learned_context(
        (experience, skill, stale_tool),
        query="count gas stations by country",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
    )
    assert not context.skills


def test_incompatible_host_and_tiny_budget_do_not_expose_partial_tool_schema() -> None:
    from powercontext.builtin.runtime.learned_context import prepare_learned_context

    artifacts = _learned_artifacts()
    wrong_host = prepare_learned_context(
        artifacts,
        query="gas stations",
        dialect="postgres",
        database_name="debit_card",
        max_bytes=8000,
    )
    assert not wrong_host.tools and not wrong_host.skills
    too_small = prepare_learned_context(
        artifacts,
        query="gas stations",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=100,
    )
    assert not too_small.tools and not too_small.skills


def test_unrelated_question_does_not_receive_a_learned_tool() -> None:
    from powercontext.builtin.runtime.learned_context import prepare_learned_context

    context = prepare_learned_context(
        _learned_artifacts(),
        query="explain database deadlocks",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
    )
    assert not context.tools and not context.skills


def test_joint_http_context_deduplicates_experience_and_obeys_combined_budget(tmp_path, monkeypatch):
    import asyncio

    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit
    from tests.e2e.test_trace_learning import Generator, bundle_data, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            imported = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert imported.status_code == 202
            await env.controller.process()
            run = (await env.client.get(f"/v1/scopes/learning/trace-learning/{imported.json()['run_id']}")).json()
            ref = ArtifactRef.model_validate(next(r for r in run["artifacts"] if r["family"] == "experience"))
            content = ExperienceContent.model_validate(bundle_data()["experiences"][0]["content"])

            async def recalled(*args):
                return (ExperienceSearchHit(artifact_ref=ref, content=content),)

            monkeypatch.setattr(env.runtime, "_experience_recall", recalled)
            payload = {
                "scope_id": "learning",
                "query": "count stations by country",
                "max_bytes": 8000,
                "assembly": {"sections": [{"family": "experience", "limit": 2}]},
            }
            legacy = (await env.client.post("/v1/context/prepare", json=payload)).json()
            assert content.lesson in legacy["content"]
            joint = (
                await env.client.post(
                    "/v1/context/prepare",
                    json={
                        **payload,
                        "learned_tools": True,
                        "host_profile": request_data()["host_profile"],
                    },
                )
            ).json()
            assert joint["status"] == "ready" and joint["content"] is None
            assert joint["content_bytes"] == 0
            learned = joint["learned_context"]
            assert learned["experiences"][0]["ref"] == ref.model_dump()
            from powercontext.builtin.runtime.learned_context import LearnedContext

            typed_bytes = len(LearnedContext.model_validate(learned).model_dump_json().encode())
            assert joint["content_bytes"] + typed_bytes <= payload["max_bytes"]

    asyncio.run(scenario())
