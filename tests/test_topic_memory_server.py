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

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.topic_memory import (
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryContent,
    TopicMemorySearchHit,
    TopicMemorySearchResult,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import TopicMemoryFlushResult, TopicMemoryProcessingUnavailableError
from powercontext.errors import ArtifactNotFoundError
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.sources import SourceRef

REFERENCE = ArtifactRef(family="topic-memory", artifact_id="supervisor", revision=3)


class _TopicMemoryApplication:
    def for_scope(self, scope_id: str, /) -> _TopicMemoryApplication:
        assert scope_id == "scope-a"
        return self

    async def search(self, request):
        assert request.query == "supervisor recovery"
        assert request.limit == 4
        return TopicMemorySearchResult(
            mode="hybrid",
            hits=(
                TopicMemorySearchHit(
                    artifact_ref=REFERENCE,
                    title="Supervisor",
                    summary="Durable recovery",
                    snippet="Pending waves recover after restart.",
                    score=0.75,
                    matched_by=("topic_fts", "detail_vector"),
                ),
            ),
        )

    async def get(self, request):
        if request.artifact.artifact_id == "missing":
            raise ArtifactNotFoundError(request.artifact)
        assert request.artifact == REFERENCE
        return PublishedTopicMemory(
            topic=TopicMemory(
                artifact_id="supervisor",
                revision=3,
                content=TopicMemoryContent(
                    title="Supervisor",
                    summary="Durable recovery",
                    detail="The database is authoritative.",
                ),
                lineage=ArtifactLineage(sources=(SourceRef(source_type="content", source_id="source-1"),)),
            ),
            published_at=datetime.now(UTC),
            is_current=True,
            current_artifact=REFERENCE,
        )

    async def flush(self):
        return TopicMemoryFlushResult(status="accepted")


def _client(application: object | None = None) -> TestClient:
    topic_memory = _TopicMemoryApplication() if application is None else application
    return TestClient(create_app(application=SimpleNamespace(topic_memory=topic_memory)))


def test_topic_memory_http_operations_return_public_progressive_disclosure_shapes() -> None:
    with _client() as client:
        search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": "scope-a", "query": "supervisor recovery", "limit": 4},
        )
        exact = client.post(
            "/v1/topic-memory/get",
            json={"scope_id": "scope-a", "artifact": REFERENCE.model_dump(mode="json")},
        )
        flush = client.post("/v1/topic-memory/flush", json={"scope_id": "scope-a"})

    assert search.status_code == 200
    assert search.json() == {
        "mode": "hybrid",
        "hits": [
            {
                "artifact": REFERENCE.model_dump(mode="json"),
                "title": "Supervisor",
                "summary": "Durable recovery",
                "snippet": "Pending waves recover after restart.",
                "score": 0.75,
                "matched_by": ["topic_fts", "detail_vector"],
            }
        ],
    }
    assert exact.status_code == 200
    assert exact.json() == {
        "artifact": REFERENCE.model_dump(mode="json"),
        "title": "Supervisor",
        "summary": "Durable recovery",
        "detail": "The database is authoritative.",
        "source_refs": [{"name": "content", "source_id": "source-1"}],
    }
    assert flush.status_code == 200
    assert flush.json() == {"status": "accepted"}


def test_topic_memory_search_rejects_caller_selected_mode() -> None:
    response = _client().post(
        "/v1/topic-memory/search",
        json={"scope_id": "scope-a", "query": "supervisor recovery", "mode": "fts"},
    )

    assert response.status_code == 422


def test_topic_memory_get_maps_missing_or_cross_scope_values_to_not_found() -> None:
    response = _client().post(
        "/v1/topic-memory/get",
        json={
            "scope_id": "scope-a",
            "artifact": {"family": "topic-memory", "artifact_id": "missing", "revision": 1},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"


def test_topic_memory_flush_maps_known_missing_processing_capability_to_503() -> None:
    class _Unavailable(_TopicMemoryApplication):
        async def flush(self):
            raise TopicMemoryProcessingUnavailableError

    response = _client(_Unavailable()).post("/v1/topic-memory/flush", json={"scope_id": "scope-a"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "topic_memory_processing_unavailable"


def test_composed_fts_runtime_fails_closed_only_for_missing_topic_processing(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        capabilities = client.get("/v1/capabilities")
        search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": "scope-a", "query": "supervisor recovery"},
        )
        missing = client.post(
            "/v1/topic-memory/get",
            json={"scope_id": "scope-a", "artifact": REFERENCE.model_dump(mode="json")},
        )
        wrong_family = client.post(
            "/v1/topic-memory/get",
            json={
                "scope_id": "scope-a",
                "artifact": {"family": "memory", "artifact_id": "memory", "revision": 1},
            },
        )
        flush = client.post("/v1/topic-memory/flush", json={"scope_id": "scope-a"})

    assert "topic-memory" in capabilities.json()["artifact_families"]
    assert search.status_code == 200
    assert search.json() == {"mode": "fts", "hits": []}
    assert missing.status_code == 404
    assert wrong_family.status_code == 422
    assert flush.status_code == 503


def test_prepared_context_focuses_65_term_topic_recall_without_losing_memory(tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    query = " ".join(f"term{index:02d}" for index in range(65))

    with TestClient(app) as client:
        remembered = client.post(
            "/v1/memory/remember",
            json={"scope_id": "scope-a", "kind": "fact", "text": query},
        )
        prepared = client.post(
            "/v1/context/prepare",
            json={"scope_id": "scope-a", "query": query},
        )
        public_topic_search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": "scope-a", "query": query},
        )

    assert remembered.status_code == 200
    assert prepared.status_code == 200
    assert prepared.json()["status"] == "ready"
    assert query in prepared.json()["content"]
    assert public_topic_search.status_code == 422
    assert public_topic_search.json()["error"]["code"] == "invalid_request"
