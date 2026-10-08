"""Atomic public API retains preview and cascade behavior through the shared service."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError, AtomicMemoryPreviewStaleError
from powercontext.builtin.artifacts.atomic_memory.restoration import AtomicMemoryPreviewSigner
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts


@pytest.mark.parametrize("target,historical", (("e", False), ("b", False), ("b", True)))
def test_atomic_cascade_restore_uses_first_revision_and_preserves_preview_contract(target, historical) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(
                runtime=RuntimeConfig(
                    atomic_memory_preview_signing_secret=SecretStr("x" * 32),
                    atomic_memory_preview_signing_key_id="test",
                )
            )
        ) as contexts:
            application = contexts.atomic_memory
            signer = AtomicMemoryPreviewSigner(keys={"test": b"x" * 32}, active_key_id="test")
            memory = application.for_scope("project")
            originals = {}
            for identity in ("a", "b", "d"):
                created = await contexts.records.create_atomic_memories(
                    "project", ({"kind": "rule", "text": f"Keep rule {identity}."},)
                )
                originals[identity] = await memory.get(created[0].artifact_id)
            first = await memory.merge(
                tuple(originals[item].as_read() for item in ("a", "b")),
                AtomicMemoryContent(kind="rule", text="Keep a and b."),
            )
            await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                first.primary.ref.artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"kind": "rule", "text": "Keep revised a and b."}),
            )
            revised = await memory.get(first.primary.ref.artifact_id)
            final = await memory.merge(
                (revised.as_read(), originals["d"].as_read()), AtomicMemoryContent(kind="rule", text="Keep a, b and d.")
            )
            identity = final.primary.ref.artifact_id if target == "e" else originals["b"].ref.artifact_id
            kwargs = {"operation": "undo_merge"} if target == "e" else {"revision": 1} if historical else {}
            preview = await memory.preview_restoration(identity, **kwargs)
            # Atomic's original token can still be decoded by the public signer.
            assert (
                signer.validate(
                    preview.preview_token,
                    scope_id="project",
                    subject=application.security.subject(application.default_context),
                    operation=kwargs.get("operation", "restore"),
                    artifact_id=identity,
                    revision=kwargs.get("revision"),
                )
                == preview.endpoint
            )
            result = await memory.restore(identity, preview_token=preview.preview_token, **kwargs)
            assert result.undo_merge_results == (
                (final.primary.ref.artifact_id,)
                if target == "e"
                else (final.primary.ref.artifact_id, first.primary.ref.artifact_id)
            )
            assert (await memory.get(final.primary.ref.artifact_id)).state.state == "retired"
            assert (await memory.get(first.primary.ref.artifact_id)).state.state == (
                "active" if target == "e" else "retired"
            )
            for name in ("a", "b"):
                record = await memory.get(originals[name].ref.artifact_id)
                assert record.state.state == ("merged" if target == "e" else "active")
                assert record.ref.revision == (2 if name == "b" and historical else 1)
            assert (await memory.get(originals["d"].ref.artifact_id)).state.state == "active"

    asyncio.run(scenario())


def test_atomic_endpoint_change_rejects_signed_preview() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(
                runtime=RuntimeConfig(
                    atomic_memory_preview_signing_secret=SecretStr("x" * 32),
                    atomic_memory_preview_signing_key_id="test",
                )
            )
        ) as contexts:
            application = contexts.atomic_memory
            memory = application.for_scope("project")
            created = await contexts.records.create_atomic_memories(
                "project", ({"kind": "rule", "text": "Keep this rule."},)
            )
            original = await memory.get(created[0].artifact_id)
            preview = await memory.preview_restoration(original.ref.artifact_id)
            await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                original.ref.artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"kind": "rule", "text": "Keep the revised rule."}),
            )
            with pytest.raises(AtomicMemoryPreviewStaleError):
                await memory.restore(original.ref.artifact_id, preview_token=preview.preview_token)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ("revision", "state"))
def test_atomic_merge_commit_rejects_changed_input_without_partial_result(change: str) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig()) as contexts:
            application = contexts.atomic_memory
            memory = application.for_scope("project")
            created = await contexts.records.create_atomic_memories(
                "project", ({"kind": "rule", "text": "First rule."}, {"kind": "rule", "text": "Second rule."})
            )
            originals = tuple([await memory.get(item.artifact_id) for item in created])
            async with contexts.database.transaction() as connection:
                plan = await application.service.inspect_merge(
                    connection,
                    "project",
                    "new-result",
                    tuple(record.as_read() for record in originals),
                    AtomicMemoryContent(kind="rule", text="Keep both rules."),
                    application.default_context,
                )
            prepared = await application.service.prepare_merge(plan)
            if change == "revision":
                await contexts.records.replace_artifact(
                    "project",
                    "atomic-memory",
                    originals[0].ref.artifact_id,
                    '"revision:1"',
                    ArtifactWrite(content={"kind": "rule", "text": "Updated first rule."}),
                )
            else:
                await memory.forget(originals[0].ref.artifact_id, expected_revision=1, expected_state_version=0)
            with pytest.raises(AtomicMemoryConflictError):
                async with contexts.database.transaction() as connection:
                    await application.service.commit(connection, prepared, application.default_context)
            current = (await memory.list(states=("active", "forgotten", "merged", "retired"))).items
            assert {record.ref.artifact_id for record in current} == {record.ref.artifact_id for record in originals}
            assert all(record.state.merged_into_id is None for record in current)
            assert await memory.get(originals[1].ref.artifact_id) == originals[1]

    asyncio.run(scenario())
