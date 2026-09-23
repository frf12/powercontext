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

"""Explicit capability selection happens before context budgeting and binding."""

import asyncio
from itertools import combinations

import pytest

from powercontext.builtin.runtime.learned_context import LearnedContext, prepare_learned_context
from tests.builtin.runtime.test_trace_learning_context import _learned_artifacts


def standalone_artifacts():
    experience, skill, tool = _learned_artifacts()
    skill = skill.model_copy(
        update={
            "content": skill.content.model_copy(
                update={
                    "instructions": "Count gas stations by country using the available database capabilities.",
                    "tool_dependencies": (),
                }
            )
        }
    )
    return experience, skill, tool


@pytest.mark.parametrize(
    "families", [subset for size in range(4) for subset in combinations(("experience", "skill", "tool"), size)]
)
def test_selects_each_requested_family_independently(families):
    result = prepare_learned_context(
        standalone_artifacts(),
        query="count gas stations by country",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
        families=families,
    )
    assert {ref.family for ref in result.refs} == set(families)


def test_standalone_skill_does_not_prevent_independent_tool_selection():
    result = prepare_learned_context(
        standalone_artifacts(),
        query="count gas stations by country",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
    )
    assert result.skills
    assert result.tools[0].name == "count_stations"


def test_tool_disabled_does_not_activate_it_through_a_skill_dependency():
    result = prepare_learned_context(
        _learned_artifacts(),
        query="count gas stations by country",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
        families=("skill",),
    )
    assert not result.refs


def test_standalone_skill_cannot_consume_the_only_tool_budget():
    _, skill, tool = standalone_artifacts()
    skill = skill.model_copy(update={"content": skill.content.model_copy(update={"instructions": "stations " * 120})})
    result = prepare_learned_context(
        (skill, tool),
        query="stations",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=1400,
    )
    assert result.tools and not result.skills
    assert len(result.model_dump_json().encode()) <= 1400


def test_can_select_multiple_distinct_tools_within_limit():
    _, _, tool = standalone_artifacts()
    tools = tuple(
        tool.model_copy(
            update={
                "artifact_id": f"stations-{i}",
                "content": tool.content.model_copy(update={"name": f"count_stations_{i}"}),
            }
        )
        for i in range(4)
    )
    result = prepare_learned_context(
        tools,
        query="stations",
        dialect="mysql",
        database_name="debit_card",
        max_bytes=8000,
        families=("tool",),
        tool_limit=2,
    )
    assert len(result.tools) == 2
    assert len({tool.name for tool in result.tools}) == 2


def test_http_explicit_tool_selection_disables_experience_fallback(tmp_path, monkeypatch):
    from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit
    from tests.e2e.test_trace_learning import Generator, bundle_data, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            imported = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            await env.controller.process()
            run = (await env.client.get(f"/v1/scopes/learning/trace-learning/{imported.json()['run_id']}")).json()
            from powercontext.artifacts import ArtifactRef

            ref = ArtifactRef.model_validate(next(ref for ref in run["artifacts"] if ref["family"] == "experience"))

            async def recalled(*args):
                return (
                    ExperienceSearchHit(
                        artifact_ref=ref,
                        content=ExperienceContent.model_validate(bundle_data()["experiences"][0]["content"]),
                    ),
                )

            monkeypatch.setattr(env.runtime, "_experience_recall", recalled)
            for families, legacy_enabled in ((["tool"], False), (["tool"], True), ([], True)):
                response = await env.client.post(
                    "/v1/context/prepare",
                    json={
                        "scope_id": "learning",
                        "query": "count stations by country",
                        "learned_families": families,
                        "learned_tools": legacy_enabled,
                        "host_profile": request_data()["host_profile"],
                        "assembly": {"sections": [{"family": "experience", "limit": 2}]},
                    },
                )
                assert response.status_code == 200, response.text
                data = response.json()
                assert not data["content"]
                learned = LearnedContext.model_validate(data.get("learned_context") or {})
                assert {ref.family for ref in learned.refs} == set(families)

    asyncio.run(scenario())


def test_http_search_returns_only_compatible_callable_tools(tmp_path):
    from powercontext.client import PowerContextClient
    from powercontext.http import SearchToolsRequest
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            await env.controller.process()
            payload = {
                "scope_id": "learning",
                "query": "count stations by country",
                "host_profile": request_data()["host_profile"],
                "limit": 1,
            }
            response = await env.client.post("/v1/tools/search", json=payload)
            assert response.status_code == 200, response.text
            assert set(response.json()) == {"tools"}
            tool = response.json()["tools"][0]
            assert tool["ref"]["family"] == "tool"
            assert tool["input_schema"]["properties"]
            assert tool["implementation"]["sql"].endswith("country = ?")
            api = PowerContextClient("http://127.0.0.1", token="author-token", http_client=env.client)  # noqa: S106
            typed = await api.search_tools(SearchToolsRequest.model_validate(payload))
            assert typed.tools[0].model_dump(mode="json") == tool
            for update in (
                {"query": "explain deadlocks"},
                {"host_profile": {**payload["host_profile"], "database_name": "other_database"}},
                {"host_profile": {**payload["host_profile"], "dialect": "postgresql"}},
                {"max_bytes": 512},
            ):
                empty = await env.client.post("/v1/tools/search", json={**payload, **update})
                assert empty.status_code == 200, empty.text
                assert empty.json() == {"tools": []}
            denied = await env.client.post(
                "/v1/tools/search", json=payload, headers={"Authorization": "Bearer unknown"}
            )
            assert denied.status_code in {401, 403}

    asyncio.run(scenario())
