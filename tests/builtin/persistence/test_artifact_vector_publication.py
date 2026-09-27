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

from pydantic import JsonValue

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.publication import ArtifactPublicationRequest
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft

PROFILE = EmbeddingProfile(
    profile_id="artifact-vector-publication-v1",
    model="test:artifact-vector-publication",
    dimension=2,
    distance="l2",
    normalization="unit",
)
_ARTIFACT_MARKERS = ("client-v1", "client-v2", "client-skill", "client-publication")


class _RecordingEmbedding:
    profile = PROFILE

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.artifact_texts: set[str] = set()
        self.reject_artifact_text = False

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(texts)
        artifact_texts = tuple(text for text in texts if any(marker in text for marker in _ARTIFACT_MARKERS))
        if self.reject_artifact_text and artifact_texts:
            raise AssertionError(  # noqa: TRY003
                f"query embedding received an artifact document: {artifact_texts[0]!r}"
            )
        self.artifact_texts.update(artifact_texts)
        return EmbeddingResult(
            vectors=tuple(
                (1.0, 0.0) if "client" in text.casefold() or "客户端" in text else (0.0, 1.0) for text in texts
            )
        )


def _experience(marker: str) -> dict[str, JsonValue]:
    return {
        "situation": f"A client workflow contains the {marker} marker.",
        "action": "Regenerate the client and inspect the resulting diff.",
        "outcome": "The checked-in client agrees with the public contract.",
        "lesson": "Regenerate the client before validating public contract changes.",
    }


def _skill() -> dict[str, JsonValue]:
    return {
        "name": "client-skill",
        "description": "Use for the client vector publication regression.",
        "instructions": "Regenerate the client and inspect the resulting diff.",
        "validation": ["The generated client matches the contract."],
    }


def _ref(created) -> ArtifactRef:
    return ArtifactRef(family=created.family, artifact_id=created.artifact_id, revision=created.revision)


def test_records_publish_experience_and_skill_vectors_and_replace_current_revision(tmp_path) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vector-publication.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            experience = await contexts.records.create_artifact(
                "project",
                "experience",
                ArtifactWrite(content=_experience("client-v1")),
            )
            skill = await contexts.records.create_artifact(
                "project",
                "skill",
                ArtifactWrite(content=_skill()),
            )
            replaced = await contexts.records.replace_artifact(
                "project",
                "experience",
                experience.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=_experience("client-v2")),
            )
            assert replaced.revision == 2
            assert any("client-v1" in text for text in model.artifact_texts)
            assert any("client-v2" in text for text in model.artifact_texts)
            assert any("client-skill" in text for text in model.artifact_texts)

            model.reject_artifact_text = True
            query_start = len(model.calls)
            experience_hits = await contexts.search_experience("project", "客户端语义查询", 8)
            skill_hits = await contexts.search_skills("project", "客户端技能", 8)

            assert tuple(hit.artifact_ref for hit in experience_hits) == (
                ArtifactRef(family="experience", artifact_id=experience.artifact_id, revision=2),
            )
            assert tuple(hit.artifact_ref for hit in skill_hits) == (_ref(skill),)
            assert all(
                not any(marker in text for marker in _ARTIFACT_MARKERS)
                for batch in model.calls[query_start:]
                for text in batch
            )

    asyncio.run(scenario())


def test_publication_commits_target_vector_and_idempotent_replay_does_not_embed_again(tmp_path) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vector-publication.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            source_scope = await contexts.scopes.create(
                ScopeDraft(title="Source", summary="Vector source", idempotency_key="vector-source")
            )
            target_scope = await contexts.scopes.create(
                ScopeDraft(title="Target", summary="Vector target", idempotency_key="vector-target")
            )
            source = await contexts.records.create_artifact(
                source_scope.scope_id,
                "experience",
                ArtifactWrite(content=_experience("client-publication")),
            )
            request = ArtifactPublicationRequest(
                source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=_ref(source)),
                target_scope_id=target_scope.scope_id,
                idempotency_key="publish-client-vector",
            )
            before_publish = len(model.calls)
            published = await contexts.publications.publish(request)
            assert len(model.calls) > before_publish

            model.reject_artifact_text = True
            query_start = len(model.calls)
            hits = await contexts.search_experience(target_scope.scope_id, "客户端跨Scope发布", 8)
            assert tuple(hit.artifact_ref for hit in hits) == (published.target.artifact,)
            assert all(
                not any(marker in text for marker in _ARTIFACT_MARKERS)
                for batch in model.calls[query_start:]
                for text in batch
            )

            before_replay = len(model.calls)
            replay = await contexts.publications.publish(request)
            assert replay == published
            assert len(model.calls) == before_replay

    asyncio.run(scenario())
