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

import pytest
from sqlalchemy import text

from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.skill import SkillContent, SkillDraft
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.inference.errors import InferenceUnavailableError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture

PROFILE = EmbeddingProfile(
    profile_id="artifact-vectors-test-v1",
    model="test",
    dimension=2,
    distance="l2",
    normalization="unit",
)
PROFILE_OTHER = EmbeddingProfile(
    profile_id="artifact-vectors-test-other-v1",
    model="other-test",
    dimension=2,
    distance="l2",
    normalization="unit",
)
PROFILE_DIMENSION_CHANGED = EmbeddingProfile(
    profile_id="artifact-vectors-test-dimension-3-v1",
    model="test",
    dimension=3,
    distance="l2",
    normalization="unit",
)


class _RecordingEmbedding:
    profile = PROFILE

    def __init__(self, profile: EmbeddingProfile = PROFILE) -> None:
        self.profile = profile
        self.calls: list[tuple[str, ...]] = []
        self.artifact_texts: set[str] = set()
        self.reject_artifact_texts = False
        self.collect_artifact_texts = True
        self.fail = False

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(texts)
        if self.fail:
            raise InferenceUnavailableError("embedding")
        if self.reject_artifact_texts and self.artifact_texts.intersection(texts):
            assert not self.artifact_texts.intersection(texts), "query embedding received an artifact document"
        if self.collect_artifact_texts:
            self.artifact_texts.update(texts)
        return EmbeddingResult(vectors=tuple(self._vector(text) for text in texts))

    @staticmethod
    def _vector(text: str) -> tuple[float, float]:
        normalized = text.casefold()
        if "client" in normalized or "客户端" in text:
            return (1.0, 0.0)
        return (0.0, 1.0)


def _experience(marker: str) -> ExperienceContent:
    return ExperienceContent(
        situation=f"A generated client contains the {marker} marker.",
        action="Regenerate the client and inspect the resulting diff.",
        outcome="The checked-in client agrees with the public contract.",
        lesson="Regenerate the client before validating public contract changes.",
    )


def _skill() -> SkillContent:
    return SkillContent(
        name="generated-client-vector-check",
        description="Use after changing the public HTTP contract.",
        instructions="Regenerate the public client and inspect the resulting diff.",
        validation=("make contract-test passes",),
    )


def test_skill_vector_rebuild_ignores_workflow_but_preserves_returned_instructions(tmp_path) -> None:
    class ProjectionEmbedding(_RecordingEmbedding):
        @staticmethod
        def _vector(text: str) -> tuple[float, float]:
            return (1.0, 0.0) if "bodyneedle" in text else (0.0, 1.0)

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'skill-projection.db'}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=ProjectionEmbedding()
        ) as contexts:
            async with contexts.database.transaction() as connection:
                skill = await contexts.repositories.artifacts.create(
                    connection,
                    "project",
                    "orbital-skill",
                    SkillDraft(
                        content=SkillContent(
                            name="orbital-planning",
                            description="Forecast eclipse observations.",
                            instructions="Run bodyneedle before returning the current observations.",
                            validation=("bodyneedle complete",),
                        )
                    ),
                )
            assert contexts.artifact_vectors is not None
            await contexts.artifact_vectors.rebuild("project")
            assert await contexts.search_skills("project", "bodyneedle", 8) == ()
            hits = await contexts.search_skills("project", "eclipse", 8)
            assert tuple(hit.artifact_ref for hit in hits) == (skill.as_ref(),)
            assert hits[0].content.instructions == skill.content.instructions

    asyncio.run(scenario())


def test_artifact_vectors_rebuild_experience_and_skill_and_embed_only_queries_after_backfill(tmp_path) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vectors.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="artifact-vector-test", content="The client repair was verified.")
            )
            source_ref = context.sources.catalog.as_ref(source)
            review = contexts.review("project")

            experience_candidate = await review.propose_experience(
                _experience("client-vector"),
                sources=(source_ref,),
                artifacts=(),
                target=None,
                reason=None,
            )
            approved_experience = await review.approve(
                experience_candidate.candidate_id,
                experience_candidate.version,
            )
            skill_candidate = await review.propose_skill(
                _skill(),
                sources=(source_ref,),
                artifacts=(),
                target=None,
                reason=None,
            )
            approved_skill = await review.approve(skill_candidate.candidate_id, skill_candidate.version)
            assert approved_experience.result_artifact is not None
            assert approved_skill.result_artifact is not None

            assert contexts.artifact_vectors is not None
            model.collect_artifact_texts = False
            model.reject_artifact_texts = True
            model.calls.clear()

            experience_hits = await contexts.search_experience("project", "客户端修复流程", 8)
            skill_hits = await contexts.search_skills("project", "公共客户端重建流程", 8)

            assert tuple(hit.artifact_ref for hit in experience_hits) == (approved_experience.result_artifact,)
            assert tuple(hit.artifact_ref for hit in skill_hits) == (approved_skill.result_artifact,)
            assert model.calls == [("客户端修复流程",), ("公共客户端重建流程",)]

    asyncio.run(scenario())


def test_artifact_vectors_are_scoped_by_family_and_scope_and_replace_revisions(tmp_path) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vectors.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            other_scope = (
                await contexts.scopes.create(
                    ScopeDraft(title="Other", summary="Vector isolation", idempotency_key="artifact-vector-other")
                )
            ).scope_id
            async with contexts.database.transaction() as connection:
                experience = await contexts.repositories.artifacts.create(
                    connection,
                    "project",
                    "same-id",
                    ExperienceDraft(content=_experience("project-experience")),
                )
                skill = await contexts.repositories.artifacts.create(
                    connection,
                    "project",
                    "same-id",
                    SkillDraft(content=_skill()),
                )
                other_experience = await contexts.repositories.artifacts.create(
                    connection,
                    other_scope,
                    "same-id",
                    ExperienceDraft(content=_experience("other-scope-experience")),
                )

            assert contexts.artifact_vectors is not None
            assert await contexts.artifact_vectors.rebuild("project") == 2
            assert await contexts.artifact_vectors.rebuild(other_scope) == 1
            calls_after_first_rebuild = len(model.calls)
            assert await contexts.artifact_vectors.rebuild("project") == 0
            assert len(model.calls) == calls_after_first_rebuild

            assert tuple(hit.artifact_ref for hit in await contexts.search_experience("project", "客户端查询", 8)) == (
                experience.as_ref(),
            )
            assert tuple(hit.artifact_ref for hit in await contexts.search_skills("project", "客户端查询", 8)) == (
                skill.as_ref(),
            )
            assert tuple(
                hit.artifact_ref for hit in await contexts.search_experience(other_scope, "其他客户端查询", 8)
            ) == (other_experience.as_ref(),)

            async with contexts.database.transaction() as connection:
                revised = await contexts.repositories.artifacts.revise(
                    connection,
                    "project",
                    experience,
                    ExperienceDraft(content=_experience("project-experience-revised")),
                )
            assert revised.revision == 2
            assert await contexts.artifact_vectors.rebuild("project") == 1
            assert tuple(hit.artifact_ref for hit in await contexts.search_experience("project", "客户端查询", 8)) == (
                revised.as_ref(),
            )

    asyncio.run(scenario())


def test_artifact_vector_provider_failure_falls_back_to_fts(tmp_path) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vectors.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="fts-fallback-test", content="The fallback repair was verified.")
            )
            candidate = await contexts.review("project").propose_experience(
                _experience("fts-fallback-marker"),
                sources=(context.sources.catalog.as_ref(source),),
                artifacts=(),
                target=None,
                reason=None,
            )
            approved = await contexts.review("project").approve(candidate.candidate_id, candidate.version)
            assert approved.result_artifact is not None

            model.fail = True
            hits = await contexts.search_experience("project", "fts-fallback-marker", 8)

            assert tuple(hit.artifact_ref for hit in hits) == (approved.result_artifact,)

    asyncio.run(scenario())


def test_embedding_failure_rolls_back_approval_and_candidate_can_retry(tmp_path) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vectors.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="embedding-rollback-test", content="The rollback evidence is complete.")
            )
            review = contexts.review("project")
            candidate = await review.propose_experience(
                _experience("embedding-rollback"),
                sources=(context.sources.catalog.as_ref(source),),
                artifacts=(),
                target=None,
                reason=None,
            )

            model.fail = True
            with pytest.raises(InferenceUnavailableError):
                await review.approve(candidate.candidate_id, candidate.version)

            pending = await review.get_candidate(candidate.candidate_id)
            assert pending.status.value == "pending"
            assert pending.result_artifact is None
            async with contexts.database.transaction() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT COUNT(*) FROM pc_artifact_heads "
                            "WHERE scope_id = 'project' AND family = 'experience'"
                        )
                    )
                    == 0
                )

            model.fail = False
            approved = await review.approve(candidate.candidate_id, candidate.version)
            assert approved.status.value == "approved"
            assert approved.result_artifact is not None

    asyncio.run(scenario())


def test_artifact_vector_write_failure_rolls_back_approval_and_candidate(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        model = _RecordingEmbedding()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-vectors.db'}")
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=model) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="index-rollback-test", content="The index write must be atomic.")
            )
            review = contexts.review("project")
            candidate = await review.propose_skill(
                _skill(),
                sources=(context.sources.catalog.as_ref(source),),
                artifacts=(),
                target=None,
                reason=None,
            )
            assert contexts.artifact_vectors is not None

            async def fail_replace(*_args, **_kwargs):
                raise RuntimeError("vector index write failed")  # noqa: TRY003

            monkeypatch.setattr(contexts.artifact_vectors.index, "replace", fail_replace)
            with pytest.raises(RuntimeError, match="vector index write failed"):
                await review.approve(candidate.candidate_id, candidate.version)

            pending = await review.get_candidate(candidate.candidate_id)
            assert pending.status.value == "pending"
            assert pending.result_artifact is None
            async with contexts.database.transaction() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT COUNT(*) FROM pc_artifact_heads WHERE scope_id = 'project' AND family = 'skill'")
                    )
                    == 0
                )

    asyncio.run(scenario())


def test_artifact_vectors_require_rebuild_after_same_dimension_profile_change(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "artifact-vectors-profile-change.db"
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        first_model = _RecordingEmbedding()
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=first_model) as contexts:
            async with contexts.database.transaction() as connection:
                artifact = await contexts.repositories.artifacts.create(
                    connection,
                    "project",
                    "profile-change",
                    ExperienceDraft(content=_experience("profile-change")),
                )
            assert contexts.artifact_vectors is not None
            assert await contexts.artifact_vectors.rebuild("project") == 1

        second_model = _RecordingEmbedding(PROFILE_OTHER)
        async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=second_model) as contexts:
            assert contexts.artifact_vectors is not None
            assert await contexts.search_experience("project", "客户端查询", 8) == ()
            assert await contexts.artifact_vectors.rebuild("project") == 1
            hits = await contexts.search_experience("project", "客户端查询", 8)

            assert tuple(hit.artifact_ref for hit in hits) == (artifact.as_ref(),)

    asyncio.run(scenario())


def test_sqlite_artifact_vector_dimension_change_requires_offline_rebuild(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "artifact-vectors-dimension-change.db"
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        async with open_builtin_contexts(
            BuiltinConfig(database=config), embedding_model=_RecordingEmbedding()
        ) as contexts:
            async with contexts.database.transaction() as connection:
                await contexts.repositories.artifacts.create(
                    connection,
                    "project",
                    "dimension-change",
                    ExperienceDraft(content=_experience("dimension-change")),
                )
            assert contexts.artifact_vectors is not None
            assert await contexts.artifact_vectors.rebuild("project") == 1

        changed = _RecordingEmbedding(PROFILE_DIMENSION_CHANGED)
        with pytest.raises(RuntimeError, match="dimension"):
            async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=changed):
                pass

    asyncio.run(scenario())
