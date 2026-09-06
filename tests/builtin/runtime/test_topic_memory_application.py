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

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec, cast

import pytest

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryContent,
    TopicMemorySearchResult,
)
from powercontext.builtin.inference import (
    EmbeddingResult,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.runtime import (
    BuiltinRuntime,
    GetTopicMemoryRequest,
    RuntimeCapabilities,
    SearchTopicMemoryRequest,
    TopicMemoryProcessingUnavailableError,
)
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError

P = ParamSpec("P")


def _async_test(function: Callable[P, Coroutine[Any, Any, None]]) -> Callable[P, None]:
    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(function(*args, **kwargs))

    return run


class _UnusedProvider:
    async def get(self, scope_id: str, /) -> Any:
        raise AssertionError(scope_id)


class _Embedding:
    profile = EmbeddingProfile(profile_id="topic-v1", model="test", dimension=2)

    def __init__(self, result: EmbeddingResult | Exception) -> None:
        self.result = result

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        assert texts == ("supervisor recovery",)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _runtime(**kwargs: Any) -> BuiltinRuntime:
    return BuiltinRuntime(
        provider=_UnusedProvider(),
        capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=()),
        **kwargs,
    )


@_async_test
async def test_topic_search_uses_fts_without_embedding() -> None:
    calls: list[dict[str, Any]] = []

    async def search(_scope: str, _query: str, **kwargs: Any) -> TopicMemorySearchResult:
        calls.append(kwargs)
        return TopicMemorySearchResult(mode="fts")

    result = (
        await _runtime(topic_memory_search=search)
        .topic_memory.for_scope("scope-a")
        .search(SearchTopicMemoryRequest(query="supervisor recovery"))
    )

    assert result.mode == "fts"
    assert calls == [{"limit": 10, "mode": "fts"}]


@_async_test
async def test_topic_search_uses_hybrid_and_hides_retrieval_controls_from_the_request() -> None:
    calls: list[dict[str, Any]] = []

    async def search(_scope: str, _query: str, **kwargs: Any) -> TopicMemorySearchResult:
        calls.append(kwargs)
        return TopicMemorySearchResult(mode="hybrid")

    embedding = _Embedding(EmbeddingResult(vectors=((0.25, 0.75),)))
    result = (
        await _runtime(
            topic_memory_search=search,
            topic_memory_embedding_model=embedding,
        )
        .topic_memory.for_scope("scope-a")
        .search(SearchTopicMemoryRequest(query="supervisor recovery", limit=4))
    )

    assert result.mode == "hybrid"
    assert calls == [
        {
            "limit": 4,
            "mode": "hybrid",
            "query_vector": (0.25, 0.75),
            "embedding_profile": embedding.profile,
        }
    ]
    assert set(SearchTopicMemoryRequest.model_fields) == {"query", "limit"}


@pytest.mark.parametrize(
    "failure",
    [InferenceUnavailableError("embed"), InferenceTimeoutError("embed", 1.0)],
)
@_async_test
async def test_topic_search_observably_falls_back_only_for_transient_embedding_failures(
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    observations: list[tuple[str, bool]] = []

    async def search(_scope: str, _query: str, **kwargs: Any) -> TopicMemorySearchResult:
        calls.append(kwargs["mode"])
        return TopicMemorySearchResult(mode="fts")

    result = (
        await _runtime(
            topic_memory_search=search,
            topic_memory_embedding_model=_Embedding(failure),
            topic_memory_search_observer=lambda mode, fallback: observations.append((mode, fallback)),
        )
        .topic_memory.for_scope("scope-a")
        .search(SearchTopicMemoryRequest(query="supervisor recovery"))
    )

    assert result.mode == "fts"
    assert calls == ["fts"]
    assert observations == [("fts", True)]
    fallback = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "topic_memory.search.embedding_fallback"
    )
    assert getattr(fallback, "mode", None) == "fts"
    assert "supervisor recovery" not in fallback.getMessage()


@_async_test
async def test_topic_search_does_not_fallback_for_invalid_embedding_shape() -> None:
    calls = 0

    async def search(_scope: str, _query: str, **_kwargs: Any) -> TopicMemorySearchResult:
        nonlocal calls
        calls += 1
        return TopicMemorySearchResult(mode="fts")

    application = _runtime(
        topic_memory_search=search,
        topic_memory_embedding_model=_Embedding(EmbeddingResult(vectors=())),
    ).topic_memory.for_scope("scope-a")

    with pytest.raises(InvalidInferenceOutputError):
        await application.search(SearchTopicMemoryRequest(query="supervisor recovery"))
    assert calls == 0


@pytest.mark.parametrize(
    "topic_request",
    [
        SearchTopicMemoryRequest(query=" padded"),
        SearchTopicMemoryRequest(query="term", limit=21),
        SearchTopicMemoryRequest(query=" ".join(f"term-{index}" for index in range(65))),
    ],
)
@_async_test
async def test_topic_search_rejects_runtime_bounds_before_repository_access(
    topic_request: SearchTopicMemoryRequest,
) -> None:
    async def search(*_args: Any, **_kwargs: Any) -> TopicMemorySearchResult:
        raise AssertionError("repository must not be called")  # noqa: TRY003

    with pytest.raises(InvalidRuntimeRequestError):
        await _runtime(topic_memory_search=search).topic_memory.for_scope("scope-a").search(topic_request)


@_async_test
async def test_topic_get_requires_exact_family_and_returns_the_exact_revision() -> None:
    reference = ArtifactRef(family="topic-memory", artifact_id="supervisor", revision=3)
    published = PublishedTopicMemory(
        topic=TopicMemory(
            artifact_id="supervisor",
            revision=3,
            content=TopicMemoryContent(title="Supervisor", summary="Recovery", detail="Full detail"),
            lineage=ArtifactLineage(),
        ),
        published_at=datetime.now(UTC),
        is_current=False,
        current_artifact=reference.model_copy(update={"revision": 4}),
    )

    async def get(scope: str, artifact: ArtifactRef) -> PublishedTopicMemory:
        assert (scope, artifact) == ("scope-a", reference)
        return published

    application = _runtime(topic_memory_get=get).topic_memory.for_scope("scope-a")
    assert await application.get(GetTopicMemoryRequest(artifact=reference)) == published
    with pytest.raises(InvalidRuntimeRequestError):
        await application.get(GetTopicMemoryRequest(artifact=reference.model_copy(update={"family": "memory"})))


@_async_test
async def test_topic_flush_acceptance_does_not_require_a_local_supervisor() -> None:
    async def flush(scope: str) -> bool:
        assert scope == "scope-a"
        return True

    result = (
        await _runtime(
            topic_memory_flush=flush,
            topic_memory_processing_available=True,
        )
        .topic_memory.for_scope("scope-a")
        .flush()
    )

    assert result.status == "accepted"


@_async_test
async def test_topic_flush_keeps_committed_acceptance_when_local_wake_fails() -> None:
    class _FailingSupervisor:
        def wake(self) -> None:
            raise OSError("wake pipe closed")  # noqa: TRY003

    async def flush(_scope: str) -> bool:
        return True

    runtime = _runtime(topic_memory_flush=flush, topic_memory_processing_available=True)
    cast(Any, runtime).artifact_processing_supervisor = _FailingSupervisor()

    result = await runtime.topic_memory.for_scope("scope-a").flush()

    assert result.status == "accepted"


@_async_test
async def test_topic_flush_rejects_only_a_known_missing_processing_binding() -> None:
    async def flush(_scope: str) -> bool:
        raise AssertionError("flush persistence must not be called")  # noqa: TRY003

    with pytest.raises(TopicMemoryProcessingUnavailableError):
        await _runtime(topic_memory_flush=flush).topic_memory.for_scope("scope-a").flush()


@_async_test
async def test_topic_flush_reports_idle_when_the_source_cursor_is_current() -> None:
    async def flush(_scope: str) -> bool:
        return False

    result = (
        await _runtime(
            topic_memory_flush=flush,
            topic_memory_processing_available=True,
        )
        .topic_memory.for_scope("scope-a")
        .flush()
    )

    assert result.status == "idle"


@_async_test
async def test_topic_flush_serializes_same_scope_persistence_requests() -> None:
    active = 0
    maximum = 0

    async def flush(_scope: str) -> bool:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return True

    application = _runtime(
        topic_memory_flush=flush,
        topic_memory_processing_available=True,
    ).topic_memory.for_scope("scope-a")

    results = await asyncio.gather(application.flush(), application.flush())
    assert [result.status for result in results] == ["accepted", "accepted"]
    assert maximum == 1
