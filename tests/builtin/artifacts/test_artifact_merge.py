"""A registered ordinary Family reuses exact merge relations and group restoration."""

from __future__ import annotations

import asyncio

from powercontext.artifacts import ArtifactLineage
from tests.builtin.persistence.contract import HandoffContent, HandoffDraft, repository_profile


class _LocalSecurity:
    def subject(self, context):
        return context

    async def lock_transaction(self, connection, scope_id, context):
        pass

    async def authorize(self, connection, scope_id, context, action, ref=None):
        pass

    async def authorize_sources(self, connection, scope_id, context, sources):
        pass

    async def establish_owner(self, connection, scope_id, artifact_id, context):
        pass


class _HandoffAdapter:
    family = "handoff"

    def draft(self, content, lineage, *, merge_inputs=(), historical=False):
        return HandoffDraft(
            content=HandoffContent.model_validate(content), **lineage.model_dump(include={"sources", "artifacts"})
        )

    async def prepare(self, content):
        return content

    def validate_prepared(self, prepared):
        pass

    async def publish(self, connection, scope_id, record, prepared, execution_context):
        pass

    async def remove(self, connection, scope_id, artifact_id):
        pass


async def _merge_tags(connection, scope_id, artifact_id, inputs):
    pass


def test_shared_family_cascade_restores_selected_inputs_and_preserves_ordinary_references() -> None:
    async def scenario() -> None:
        from powercontext.builtin.artifacts.merge import ArtifactMergeService
        from powercontext.builtin.runtime.artifact_merge import ArtifactMergeApplication

        async with repository_profile() as (profile, repositories):
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                adapter=_HandoffAdapter(),
                security=_LocalSecurity(),
                merge_tags=_merge_tags,
            )
            scoped = ArtifactMergeApplication(profile.database, (service,), default_context="owner").for_scope(
                "project", "handoff"
            )
            async with profile.database.transaction() as connection:
                for identity in ("a", "b", "d", "evidence"):
                    await repositories.artifacts.create(
                        connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                    )

            async def merge(result, identities, evidence=()):
                inputs = tuple([(await scoped.get(identity)).as_read() for identity in identities])
                return await scoped.merge(
                    inputs,
                    HandoffContent(summary=result),
                    artifact_id=result,
                    lineage=ArtifactLineage(artifacts=(*evidence, *(read.ref for read in reversed(inputs)))),
                )

            evidence = (await scoped.get("evidence")).ref
            first = await merge("c", ("a", "b"), (evidence,))
            await merge("e", ("c", "d"))
            frozen = await scoped.get("a", revision=1)
            assert frozen.artifact.content.summary == "a"
            assert frozen.state.merged_into_id == "c"
            async with profile.database.transaction() as connection:
                assert await repositories.artifacts.merge_inputs(connection, "project", first.primary.ref) == tuple(
                    reversed(tuple(record.ref for record in first.records if record.ref.artifact_id in {"a", "b"}))
                )
            restored = await scoped.restore("b")
            assert restored.undo_merge_results == ("e", "c")
            for identity in ("a", "b", "d", "evidence"):
                current = await scoped.get(identity)
                assert current.state.lifecycle_state == "active"
                assert current.state.merged_into_id is None
            for identity in ("c", "e"):
                assert (await scoped.get(identity)).state.lifecycle_state == "retired"
            assert (await scoped.get("evidence")).state.governance_generation == 0

    asyncio.run(scenario())


def test_shared_ordinary_deprecated_revision_preserves_replacement_until_explicit_restore() -> None:
    async def scenario() -> None:
        from powercontext.builtin.artifacts.merge import ArtifactMergeService
        from powercontext.builtin.persistence.artifact_governance import (
            ArtifactGovernanceRepository,
            ArtifactLifecycleState,
        )

        async with repository_profile() as (profile, repositories):
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                adapter=_HandoffAdapter(),
                security=_LocalSecurity(),
                merge_tags=_merge_tags,
            )
            governance = ArtifactGovernanceRepository()
            async with profile.database.transaction() as connection:
                for identity in ("original", "replacement"):
                    await repositories.artifacts.create(
                        connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                    )
                await governance.transition(
                    connection, "project", "handoff", "original", 0, ArtifactLifecycleState.DEPRECATED, "replacement"
                )
                read = await service.get(connection, "project", "original", "owner")
                assert read.state.replacement_artifact_id == "replacement" and read.state.merged_into_id is None
                plan = await service.inspect_change(
                    connection,
                    "project",
                    "original",
                    HandoffContent(summary="revised"),
                    "owner",
                    expected_revision=1,
                    expected_state_version=1,
                )
            prepared = await service.prepare_change(plan)
            async with profile.database.transaction() as connection:
                result = await service.commit(connection, prepared, "owner")
                assert result.primary.ref.revision == 2
                assert result.primary.state.lifecycle_state == "deprecated"
                assert result.primary.state.replacement_artifact_id == "replacement"
                assert result.primary.state.governance_generation == 1
                plan = await service.inspect_restore(connection, "project", "original", "owner")
            prepared = await service.prepare_restore(plan)
            async with profile.database.transaction() as connection:
                restored = await service.commit(connection, prepared, "owner")
                assert restored.primary.state.lifecycle_state == "active"
                assert restored.primary.state.replacement_artifact_id is None
                assert restored.primary.state.governance_generation == 2
                assert restored.primary.ref.revision == 2

    asyncio.run(scenario())
