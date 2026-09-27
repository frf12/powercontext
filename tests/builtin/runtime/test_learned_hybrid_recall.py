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

"""Learned context combines lexical and embedding recall with a safe fallback."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUnavailableError


class _StationEmbeddingModel:
    profile = EmbeddingProfile(
        profile_id="learned-recall-v1",
        model="test:station-embedding",
        dimension=2,
        distance="l2",
        normalization="unit",
    )

    def __init__(self, *, profile: EmbeddingProfile | None = None, reject_artifact_text: bool = False):
        if profile is not None:
            self.profile = profile
        self.calls: list[tuple[str, ...]] = []
        self.artifact_texts: list[str] = []
        self.reject_artifact_text = reject_artifact_text

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(texts)
        artifact_texts = tuple(
            text
            for text in texts
            if any(marker in text for marker in ("Counting stations by country.", "count_stations", "count-stations"))
        )
        self.artifact_texts.extend(artifact_texts)
        if self.reject_artifact_text and artifact_texts:
            raise AssertionError(  # noqa: TRY003
                f"artifact text embedded during query phase: {artifact_texts[0]!r}"
            )
        return EmbeddingResult(
            vectors=tuple(
                (1.0, 0.0) if "按国家统计加油站数量" in text or "station" in text.casefold() else (0.0, 1.0)
                for text in texts
            )
        )


class _UnavailableEmbeddingModel:
    profile = _StationEmbeddingModel.profile

    async def embed(self, _texts: tuple[str, ...], /) -> EmbeddingResult:
        raise InferenceUnavailableError("embed")


class _ModerateSimilarityEmbeddingModel(_StationEmbeddingModel):
    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        result = await super().embed(texts)
        return result.model_copy(
            update={
                "vectors": tuple(
                    (0.6, 0.8) if "threshold check" in text else vector
                    for text, vector in zip(texts, result.vectors, strict=True)
                )
            }
        )


def test_request_rrf_controls_apply_to_both_endpoints_without_leaking(tmp_path):
    from powercontext.client import PowerContextClient
    from powercontext.http import PrepareContextRequest, SearchToolsRequest
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            api = PowerContextClient("http://127.0.0.1", http_client=env.client)
            # A semantic match with no lexical overlap exercises the RRF cutoff,
            # independently of the vector's perfect cosine similarity.
            base = {
                "scope_id": "learning",
                "query": "按国家统计加油站数量",
                "host_profile": request_data()["host_profile"],
            }
            for options, expected in (
                (None, 1),
                ({"fts_weight": 1, "vector_weight": 2, "min_rrf_score": 0.6}, 1),
                ({"fts_weight": 1, "vector_weight": 2, "min_rrf_score": 0.8}, 0),
                ({"fts_weight": 2, "vector_weight": 1, "min_rrf_score": 0.6}, 0),
                (None, 1),
            ):
                extra = {} if options is None else {"learned_retrieval_options": options}
                prepared = await api.prepare_context(
                    PrepareContextRequest.model_validate({
                        **base,
                        **extra,
                        "learned_families": ["tool"],
                        "assembly": {"sections": []},
                    })
                )
                assert len(prepared.learned_context.tools if prepared.learned_context else ()) == expected
                searched = await api.search_tools(SearchToolsRequest.model_validate(base | extra))
                assert len(searched.tools) == expected
            for options in (
                {"fts_weight": -1},
                {"vector_weight": -1},
                {"min_rrf_score": 1.1},
                {"min_rrf_score": -0.1},
                {"fts_weight": 0, "vector_weight": 0},
            ):
                for path in ("/v1/context/prepare", "/v1/tools/search"):
                    response = await env.client.post(path, json=base | {"learned_retrieval_options": options})
                    assert response.status_code == 422, response.text

    asyncio.run(scenario())


def test_rrf_cutoff_stays_strict_during_vector_failure_and_experience_assembly(tmp_path):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            vectors = env.runtime._trace_learning_service.contexts.artifact_vectors
            vectors.embedding = _UnavailableEmbeddingModel()
            base = {
                "scope_id": "learning",
                "query": "count stations by country",
                "host_profile": request_data()["host_profile"],
                "learned_tools": True,
            }
            for options, expected in (
                ({}, True),
                ({"learned_retrieval_options": {"vector_weight": 2, "min_rrf_score": 0.8}}, False),
                ({}, True),
            ):
                response = await env.client.post("/v1/context/prepare", json=base | options)
                assert response.status_code == 200, response.text
                learned = response.json().get("learned_context") or {}
                assert bool(learned.get("experiences")) == expected
                if not expected:
                    assert not learned
                    assert response.json()["content"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_enabled", [False, True])
@pytest.mark.parametrize("model_field", ["generation_model", "rerank_model"])
def test_skill_rerank_is_opt_in_per_request_with_configured_inference(
    tmp_path, monkeypatch, legacy_enabled, model_field
):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from powercontext.builtin.runtime import InferenceConfig
    from powercontext.client import PowerContextClient
    from powercontext.http import PrepareContextRequest
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    observed = []

    def respond(messages, info):
        observed.append(messages)
        return ModelResponse(parts=[TextPart('{"selected_ranks": []}')])

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr("powercontext.builtin.runtime.composition._open_pydantic_ai_model", open_model)

    async def scenario():
        async with learning_http(
            tmp_path,
            Generator(),
            embedding_model=_StationEmbeddingModel(),
            runtime_overrides={"learned_skill_rerank_enabled": legacy_enabled},
            inference=InferenceConfig.model_validate({model_field: "test:skill-rerank"}),
        ) as env:
            await _import_trace(env)
            api = PowerContextClient("http://127.0.0.1", http_client=env.client)
            base = {
                "scope_id": "learning",
                "query": "Count stations by country",
                "host_profile": request_data()["host_profile"],
                "learned_tools": True,
                "assembly": {"sections": []},
            }
            for option, expected_calls, expected_skills in (
                ({}, 0, 1),
                ({"learned_skill_rerank": False}, 0, 1),
                ({"learned_skill_rerank": True}, 1, 0),
                ({"learned_skill_rerank": False}, 1, 1),
                ({"learned_skill_rerank": True, "learned_families": ["tool"]}, 1, 0),
                ({}, 1, 1),
            ):
                prepared = await api.prepare_context(PrepareContextRequest.model_validate(base | option))
                assert prepared.learned_context is not None
                assert len(prepared.learned_context.skills) == expected_skills
                assert len(prepared.learned_context.tools) == 1
                assert len(observed) == expected_calls
            # Disabling the check must also restore the semantic admission floor.
            response = await env.client.post("/v1/context/prepare", json=base | {"query": "country network routing"})
            assert response.status_code == 200, response.text
            assert not response.json().get("learned_context")
            assert len(observed) == 1

    asyncio.run(scenario())


def test_requested_skill_rerank_requires_configured_model(tmp_path):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator()) as env:
            await _import_trace(env)
            response = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "Count stations by country",
                    "host_profile": request_data()["host_profile"],
                    "learned_tools": True,
                    "learned_skill_rerank": True,
                    "assembly": {"sections": []},
                },
            )
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "capability_not_supported"
            assert response.json()["error"]["details"]["capability"] == "learned-skill-rerank"

    asyncio.run(scenario())


@pytest.mark.parametrize("selection", ["empty", "error", "selected"])
def test_skill_applicability_can_omit_all_skills_without_removing_independent_tools(tmp_path, selection):
    from powercontext.builtin.artifacts.skill.reranking import LLMSkillReranker, SkillRerankOutput
    from powercontext.builtin.inference import GenerationResult
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    observed = []

    class RerankGenerator:
        async def generate(self, value, /):
            observed.append(value)
            if selection == "error":
                raise InferenceUnavailableError("generate")
            return GenerationResult(output=SkillRerankOutput(selected_ranks=(1,) if selection == "selected" else ()))

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            env.runtime._learned_skill_reranker = LLMSkillReranker(RerankGenerator())
            response = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "Count stations by country",
                    "host_profile": request_data()["host_profile"],
                    "learned_tools": True,
                    "learned_skill_rerank": True,
                    "assembly": {"sections": []},
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()["learned_context"]
            assert len(result["skills"]) == (1 if selection == "selected" else 0)
            assert len(result["tools"]) == 1
            assert observed
            assert set(observed[0].candidates[0].model_dump()) == {"rank", "name", "description"}

    asyncio.run(scenario())


def test_skill_applicability_sees_semantic_candidates_below_injection_floor(tmp_path):
    from powercontext.builtin.artifacts.skill.reranking import LLMSkillReranker, SkillRerankOutput
    from powercontext.builtin.inference import GenerationResult
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    class RerankGenerator:
        async def generate(self, value, /):
            assert value.candidates
            return GenerationResult(output=SkillRerankOutput(selected_ranks=(1,)))

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            env.runtime._learned_skill_reranker = LLMSkillReranker(RerankGenerator())
            response = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "给出每个国家的网点数",
                    "host_profile": request_data()["host_profile"],
                    "learned_tools": True,
                    "learned_skill_rerank": True,
                    "assembly": {"sections": []},
                },
            )
            assert response.status_code == 200, response.text
            result = response.json()["learned_context"]
            assert len(result["skills"]) == 1
            assert not result["experiences"]  # Their semantic threshold still applies.
            assert result["skills"][0]["tool_dependencies"] == [result["tools"][0]["ref"]]

    asyncio.run(scenario())


def test_denied_skill_metadata_never_reaches_applicability_model(tmp_path):
    from powercontext.builtin.artifacts.skill.reranking import LLMSkillReranker
    from powercontext.builtin.runtime.models import PrepareContextRequest
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    observed = []

    class RerankGenerator:
        async def generate(self, value, /):
            observed.append(value)
            raise AssertionError("Denied Skill reached the model")  # noqa: TRY003

    async def deny_artifacts(refs):
        assert all(ref.family == "skill" for ref in refs)
        raise PermissionError("skill denied")  # noqa: TRY003

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            env.runtime._learned_skill_reranker = LLMSkillReranker(RerankGenerator())
            with pytest.raises(PermissionError, match="skill denied"):
                await env.runtime.context.for_scope("learning").prepare(
                    PrepareContextRequest.model_validate({
                        "query": "Count stations by country",
                        "host_profile": request_data()["host_profile"],
                        "learned_tools": True,
                        "learned_skill_rerank": True,
                        "assembly": {"sections": []},
                    }),
                    authorize_artifacts=deny_artifacts,
                )
            assert not observed

    asyncio.run(scenario())


def test_http_low_similarity_lexical_hits_cannot_fill_learned_or_regular_context(tmp_path):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            query = "Explain network routing in a country."
            host = request_data()["host_profile"]
            for sections in ([], [{"family": "experience", "limit": 2}]):
                response = await env.client.post(
                    "/v1/context/prepare",
                    json={
                        "scope_id": "learning",
                        "query": query,
                        "host_profile": host,
                        "learned_tools": True,
                        "assembly": {"sections": sections},
                    },
                )
                assert response.status_code == 200, response.text
                data = response.json()
                assert not data.get("learned_context"), data
                assert not data["content"], data
            response = await env.client.post(
                "/v1/tools/search", json={"scope_id": "learning", "query": query, "host_profile": host}
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"tools": []}
            contexts = env.runtime._trace_learning_service.contexts
            assert not await contexts.search_experience("learning", query, 2)
            assert not await contexts.search_skills("learning", query, 2)

    asyncio.run(scenario())


@pytest.mark.parametrize(("threshold", "expected_tool_count"), [(0.5, 1), (0.7, 0)])
def test_runtime_similarity_threshold_controls_both_learned_http_endpoints(tmp_path, threshold, expected_tool_count):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(
            tmp_path,
            Generator(),
            embedding_model=_ModerateSimilarityEmbeddingModel(),
            runtime_overrides={"artifact_recall_min_similarity": threshold},
        ) as env:
            await _import_trace(env)
            request = {
                "scope_id": "learning",
                "query": "Count stations by country with threshold check",
                "host_profile": request_data()["host_profile"],
            }
            prepared = await env.client.post(
                "/v1/context/prepare",
                json={**request, "learned_tools": True, "assembly": {"sections": []}},
            )
            assert prepared.status_code == 200, prepared.text
            learned = prepared.json().get("learned_context") or {}
            assert len(learned.get("tools", [])) == expected_tool_count
            searched = await env.client.post("/v1/tools/search", json=request)
            assert searched.status_code == 200, searched.text
            assert len(searched.json()["tools"]) == expected_tool_count

    asyncio.run(scenario())


def test_request_skill_threshold_only_filters_skills_and_does_not_persist(tmp_path):
    from powercontext.client import PowerContextClient
    from powercontext.http import PrepareContextRequest
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_ModerateSimilarityEmbeddingModel()) as env:
            await _import_trace(env)
            api = PowerContextClient("http://127.0.0.1", http_client=env.client)
            base = {
                "scope_id": "learning",
                "query": "Count stations by country with threshold check",
                "host_profile": request_data()["host_profile"],
                "learned_tools": True,
                "learned_skill_rerank": False,
                "assembly": {"sections": []},
            }
            for option, expected_skills in (
                ({}, 1),
                ({"learned_skill_min_similarity": 0.7}, 0),
                ({"learned_skill_min_similarity": 0.5}, 1),
                ({"learned_skill_min_similarity": 0}, 1),
                ({"learned_skill_min_similarity": 1}, 0),
                ({}, 1),
            ):
                result = (
                    await api.prepare_context(PrepareContextRequest.model_validate(base | option))
                ).learned_context
                assert result is not None
                assert len(result.skills) == expected_skills
                assert len(result.experiences) == len(result.tools) == 1
            for value in (-0.1, 1.1):
                response = await env.client.post(
                    "/v1/context/prepare", json=base | {"learned_skill_min_similarity": value}
                )
                assert response.status_code == 422, response.text

    asyncio.run(scenario())


def test_explicit_skill_threshold_applies_before_optional_model_check(tmp_path):
    from powercontext.builtin.artifacts.skill.reranking import LLMSkillReranker
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    class UnexpectedRerank:
        async def generate(self, value, /):
            raise AssertionError("No Skill cleared the explicit threshold")  # noqa: TRY003

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_ModerateSimilarityEmbeddingModel()) as env:
            await _import_trace(env)
            env.runtime._learned_skill_reranker = LLMSkillReranker(UnexpectedRerank())
            response = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "Count stations by country with threshold check",
                    "host_profile": request_data()["host_profile"],
                    "learned_tools": True,
                    "learned_skill_rerank": True,
                    "learned_skill_min_similarity": 0.7,
                    "assembly": {"sections": []},
                },
            )
            assert response.status_code == 200, response.text
            learned = response.json()["learned_context"]
            assert not learned["skills"]
            assert len(learned["experiences"]) == len(learned["tools"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("fusion_options", [None, {"vector_weight": 2, "min_rrf_score": 0.6}])
def test_matching_skill_keeps_required_tool_below_independent_similarity_floor(tmp_path, fusion_options):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    class DependencyEmbedding(_StationEmbeddingModel):
        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            result = await super().embed(texts)
            return result.model_copy(
                update={
                    "vectors": tuple(
                        (0.0, 1.0) if text.startswith("count_stations\n") else vector
                        for text, vector in zip(texts, result.vectors, strict=True)
                    )
                }
            )

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=DependencyEmbedding()) as env:
            await _import_trace(env)
            request = {
                "scope_id": "learning",
                "query": "按国家统计加油站数量",
                "host_profile": request_data()["host_profile"],
                "learned_retrieval_options": fusion_options,
            }
            response = await env.client.post(
                "/v1/context/prepare",
                json={**request, "learned_tools": True, "assembly": {"sections": []}},
            )
            assert response.status_code == 200, response.text
            learned = response.json()["learned_context"]
            assert [item["name"] for item in learned["tools"]] == ["count_stations"]
            assert learned["skills"][0]["tool_dependencies"] == [learned["tools"][0]["ref"]]
            response = await env.client.post("/v1/tools/search", json=request)
            assert response.status_code == 200, response.text
            assert response.json() == {"tools": []}

    asyncio.run(scenario())


class _RetiringEmbeddingModel(_StationEmbeddingModel):
    def __init__(self):
        super().__init__()
        self.retire = None
        self.retired = False

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if self.retire is not None and not self.retired:
            self.retired = True
            await self.retire()
        return await super().embed(texts)


@pytest.mark.parametrize("workflow", ["legacy", "candidate"])
def test_http_learned_recall_uses_embedding_for_cross_language_skill(tmp_path, workflow):
    from tests.e2e.test_trace_learning import Generator, candidate_generator_for, candidate_spec, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        embedding = _StationEmbeddingModel()
        if workflow == "candidate":
            skill_spec = candidate_spec("skill", "count-stations") | {"tool_keys": ["count-country"]}
            generator, _ = candidate_generator_for([
                candidate_spec("experience", "station-country"),
                candidate_spec("tool", "count-country"),
                skill_spec,
            ])
        else:
            generator = Generator()
        async with learning_http(tmp_path, generator, embedding_model=embedding) as env:
            imported = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert imported.status_code == 202, imported.text
            await env.controller.process()
            markers = ("Counting stations by country.", "count_stations", "count-stations")
            assert all(any(marker in text for text in embedding.artifact_texts) for marker in markers)
            embedding.reject_artifact_text = True

            prepared = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "按国家统计加油站数量",
                    "assembly": {"sections": []},
                    "learned_tools": True,
                    "host_profile": request_data()["host_profile"],
                    "max_bytes": 8000,
                },
            )
            assert prepared.status_code == 200, prepared.text
            learned = prepared.json().get("learned_context")
            assert learned is not None, prepared.text
            assert len(learned["skills"]) == 1
            assert "count_stations" in learned["skills"][0]["instructions"]
            assert [tool["name"] for tool in learned["tools"]] == ["count_stations"]
            assert learned["skills"][0]["tool_dependencies"] == [learned["tools"][0]["ref"]]

    asyncio.run(scenario())


def test_http_learned_recall_falls_back_to_fts_when_embedding_is_unavailable(tmp_path):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        embedding = _StationEmbeddingModel()
        async with learning_http(tmp_path, Generator(), embedding_model=embedding) as env:
            imported = await env.client.post("/v1/scopes/learning/trace-learning", json=request_data())
            assert imported.status_code == 202, imported.text
            await env.controller.process()
            service = env.runtime._trace_learning_service
            assert service is not None
            vectors = service.contexts.artifact_vectors
            assert vectors is not None
            vectors.embedding = _UnavailableEmbeddingModel()

            prepared = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "count stations by country",
                    "assembly": {"sections": []},
                    "learned_tools": True,
                    "host_profile": request_data()["host_profile"],
                    "max_bytes": 8000,
                },
            )
            assert prepared.status_code == 200, prepared.text
            learned = prepared.json().get("learned_context")
            assert learned is not None, prepared.text
            assert [tool["name"] for tool in learned["tools"]] == ["count_stations"]
            assert learned["skills"][0]["tool_dependencies"] == [learned["tools"][0]["ref"]]

    asyncio.run(scenario())


async def _import_trace(env, *, key="first", trace_id="trace-1"):
    from tests.e2e.test_trace_learning import request_data

    accepted = await env.client.post(
        "/v1/scopes/learning/trace-learning",
        json=request_data(key, trace_id),
    )
    assert accepted.status_code == 202, accepted.text
    await env.controller.process()
    run = await env.client.get(f"/v1/scopes/learning/trace-learning/{accepted.json()['run_id']}")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "succeeded", run.text
    return run.json()


async def _learned_artifacts(env):
    service = env.runtime._trace_learning_service
    assert service is not None
    async with service.database.transaction() as connection:
        return await service.learned_artifacts(connection, "learning")


def test_learned_artifact_vectors_are_written_and_reused_after_runtime_restart(tmp_path):
    from tests.e2e.test_trace_learning import Generator
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        first_model = _StationEmbeddingModel()
        async with learning_http(tmp_path, Generator(), embedding_model=first_model) as env:
            await _import_trace(env)
            markers = ("Counting stations by country.", "count_stations", "count-stations")
            assert all(any(marker in text for text in first_model.artifact_texts) for marker in markers)

            first_model.reject_artifact_text = True
            query = "按国家统计加油站数量"
            query_call_start = len(first_model.calls)
            prepared = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": query,
                    "assembly": {"sections": []},
                    "learned_tools": True,
                    "host_profile": {
                        "kind": "datus",
                        "dialect": "sqlite",
                        "database_name": "cards",
                    },
                    "max_bytes": 8000,
                },
            )
            assert prepared.status_code == 200, prepared.text
            learned = prepared.json().get("learned_context")
            assert learned is not None, prepared.text
            assert [tool["name"] for tool in learned["tools"]] == ["count_stations"]
            assert learned["skills"][0]["tool_dependencies"] == [learned["tools"][0]["ref"]]
            assert first_model.calls[query_call_start:] == [(query,)]

            tools_search = await env.client.post(
                "/v1/tools/search",
                json={
                    "scope_id": "learning",
                    "query": query,
                    "host_profile": {
                        "kind": "datus",
                        "dialect": "sqlite",
                        "database_name": "cards",
                    },
                    "limit": 1,
                },
            )
            assert tools_search.status_code == 200, tools_search.text
            assert [tool["ref"] for tool in tools_search.json()["tools"]] == [learned["tools"][0]["ref"]]
            assert first_model.calls[query_call_start:] == [(query,), (query,)]

        same_profile_model = _StationEmbeddingModel()
        async with learning_http(tmp_path, Generator(), embedding_model=same_profile_model) as env:
            same_profile_model.reject_artifact_text = True
            query = "按国家统计加油站数量"
            prepared = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": query,
                    "assembly": {"sections": []},
                    "learned_tools": True,
                    "host_profile": {
                        "kind": "datus",
                        "dialect": "sqlite",
                        "database_name": "cards",
                    },
                    "max_bytes": 8000,
                },
            )
            assert prepared.status_code == 200, prepared.text
            learned = prepared.json().get("learned_context")
            assert learned is not None, prepared.text
            assert [tool["name"] for tool in learned["tools"]] == ["count_stations"]
            assert same_profile_model.calls == [(query,)]

    asyncio.run(scenario())


def test_prepare_drops_skill_when_its_tool_retires_during_embedding(tmp_path):
    from powercontext.builtin.persistence.artifact_governance import ArtifactLifecycleState
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        embedding = _RetiringEmbeddingModel()
        async with learning_http(tmp_path, Generator(), embedding_model=embedding) as env:
            await _import_trace(env)
            service = env.runtime._trace_learning_service
            assert service is not None
            artifacts = await _learned_artifacts(env)
            tool = next(artifact for artifact in artifacts if artifact.as_ref().family == "tool")

            async def retire_tool():
                async with service.database.transaction() as connection:
                    governance = await service.contexts.repositories.governance.get(
                        connection, "learning", "tool", tool.artifact_id
                    )
                    await service.contexts.repositories.governance.transition(
                        connection,
                        "learning",
                        "tool",
                        tool.artifact_id,
                        governance.governance_generation,
                        ArtifactLifecycleState.RETIRED,
                        None,
                    )

            embedding.retire = retire_tool
            prepared = await env.client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": "learning",
                    "query": "count stations by country",
                    "assembly": {"sections": []},
                    "learned_families": ["skill", "tool"],
                    "host_profile": request_data()["host_profile"],
                    "max_bytes": 8000,
                },
            )
            assert prepared.status_code == 200, prepared.text
            assert embedding.retired
            learned = prepared.json().get("learned_context")
            assert learned is None or not learned["skills"], prepared.text
            assert learned is None or not learned["tools"], prepared.text

    asyncio.run(scenario())


def test_learned_artifact_revision_replaces_old_revision_in_ranking(tmp_path):
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.runtime.learned_retrieval import artifact_key
    from tests.e2e.test_trace_learning import Generator
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            first_run = await _import_trace(env)
            old_refs = tuple(ArtifactRef.model_validate(ref) for ref in first_run["artifacts"])
            second_run = await _import_trace(env, key="second", trace_id="trace-2")
            new_refs = tuple(ArtifactRef.model_validate(ref) for ref in second_run["artifacts"])
            old_keys = {artifact_key(ref) for ref in old_refs}
            new_keys = {artifact_key(ref) for ref in new_refs}
            assert old_keys and old_keys.isdisjoint(new_keys)

            service = env.runtime._trace_learning_service
            assert service is not None
            async with service.database.transaction() as connection:
                old_artifacts = tuple([
                    await service.contexts.repositories.artifacts.get(connection, "learning", ref) for ref in old_refs
                ])
                current_artifacts = await service.learned_artifacts(connection, "learning")
            ranking = await service.retrieval.rank(
                "learning",
                (*old_artifacts, *current_artifacts),
                "count stations by country",
            )
            assert ranking.scores
            assert set(ranking.scores).issubset(new_keys)
            assert old_keys.isdisjoint(ranking.scores)

    asyncio.run(scenario())


def test_learned_recall_respects_unrelated_query_and_family_switches(tmp_path):
    from tests.e2e.test_trace_learning import Generator, request_data
    from tests.test_trace_learning_http import learning_http

    async def scenario():
        async with learning_http(tmp_path, Generator(), embedding_model=_StationEmbeddingModel()) as env:
            await _import_trace(env)
            common = {
                "scope_id": "learning",
                "assembly": {"sections": []},
                "host_profile": request_data()["host_profile"],
                "max_bytes": 8000,
            }

            unrelated = await env.client.post(
                "/v1/context/prepare",
                json={**common, "query": "完全无关的问题", "learned_tools": True},
            )
            assert unrelated.status_code == 200, unrelated.text
            assert unrelated.json().get("learned_context") is None, unrelated.text

            tools_only = await env.client.post(
                "/v1/context/prepare",
                json={**common, "query": "count stations by country", "learned_families": ["tool"]},
            )
            assert tools_only.status_code == 200, tools_only.text
            learned_tools = tools_only.json().get("learned_context")
            assert learned_tools is not None, tools_only.text
            assert not learned_tools["skills"]
            assert [tool["name"] for tool in learned_tools["tools"]] == ["count_stations"]

            skills_only = await env.client.post(
                "/v1/context/prepare",
                json={**common, "query": "count stations by country", "learned_families": ["skill"]},
            )
            assert skills_only.status_code == 200, skills_only.text
            assert skills_only.json().get("learned_context") is None, skills_only.text

    asyncio.run(scenario())
