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

"""Exact entry evidence survives frozen collection migration and reuse."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select, text, tuple_

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.experience.recurrence import (
    RecurrenceMatch,
    TaskOutcomeItemRef,
    candidate_set_digest,
    item_digest,
)
from powercontext.builtin.artifacts.handoff.models import (
    HandoffArtifactCitation,
    HandoffContent,
    HandoffMemoryCitation,
    HandoffSourceCitation,
    HandoffStatement,
)
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.artifacts.memory.canonical import canonical_json, entry_content_hash, normalize_refs
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamRecord, DreamRun
from powercontext.builtin.evidence.models import EvidenceManifest, EvidenceNode
from powercontext.builtin.evidence.resolver import EvidenceResolver, evidence_id
from powercontext.builtin.evidence.selection import select_evidence
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.migrations.atomic_memory_archive import ARCHIVE_TABLE
from powercontext.builtin.persistence.migrations.atomic_memory_references import DECISIONS_FORMAT, load_decisions
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
    apply_atomic_memory_migration,
    verify_atomic_memory_migration,
)
from powercontext.builtin.persistence.recurrence import RecurrenceRepository
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    SCOPES_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from powercontext.builtin.sources.content import ContentSource, ContentSourceInternal, ContentSourceTarget
from powercontext.builtin.work.models import TaskOutcome, WorkClaim
from powercontext.server.authz import ArtifactOwnerRelation, MemoryEntrySelector, PrincipalRef, ResourceRef
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.sources import SourceMaterialization, SourceRef

SCOPE = "migration-evidence"
COLLECTION = "legacy-memory"
A = SourceRef(source_type="content", source_id="task-a")
B = SourceRef(source_type="content", source_id="task-b")
C = SourceRef(source_type="content", source_id="task-c")
INTERNAL = SourceRef(source_type="content", source_id="legacy-entry-write")
INTERNAL_TEXT = "This legacy write receipt is provenance, not task evidence."
EA = ArtifactRef(family="experience", artifact_id="task-a-experience", revision=1)
EB = ArtifactRef(family="experience", artifact_id="task-b-experience", revision=1)


def _collection(revision: int) -> ArtifactRef:
    return ArtifactRef(family="memory", artifact_id=COLLECTION, revision=revision)


def _atomic(entry_id: str, revision: int) -> ArtifactRef:
    return ArtifactRef(
        family="atomic-memory",
        artifact_id=legacy_entry_artifact_id(SCOPE, COLLECTION, entry_id),
        revision=revision,
    )


def _version(
    entry_id: str, version: int, text: str, sources: tuple[SourceRef, ...], artifacts: tuple[ArtifactRef, ...]
) -> dict[str, Any]:
    refs = normalize_refs(tuple(source.model_dump(mode="json") for source in sources))
    artifact_refs = normalize_refs(tuple(artifact.model_dump(mode="json") for artifact in artifacts))
    return {
        "entry_id": entry_id,
        "entry_version_id": f"{entry_id}-v{version}",
        "version": version,
        "previous_version_id": None if version == 1 else f"{entry_id}-v{version - 1}",
        "kind": "fact",
        "text": text,
        "source_refs": canonical_json(refs),
        "artifact_refs": canonical_json(artifact_refs),
        "entry_content_hash": entry_content_hash(kind="fact", text=text, source_refs=refs, artifact_refs=artifact_refs),
        "created_in_revision": version,
    }


async def _seed(config: SQLiteConfig, extra=None) -> bytes:
    alpha = _version("alpha", 1, "Task A was completed.", (A, INTERNAL), (EA,))
    beta = _version("beta", 1, "Unrelated task B was completed.", (B,), (EB,))
    revised = _version("alpha", 2, "Tasks A and C were completed.", (A, C, INTERNAL), (EA,))
    async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
        await contexts.get(SCOPE)
        async with contexts.database.transaction() as connection:
            await create_tables(connection, (MEMORY_ENTRY_VERSIONS_TABLE,))
            for source in (A, B, C):
                await contexts.repositories.sources.add(
                    connection,
                    SCOPE,
                    ContentSource(
                        name=source.source_id,
                        materialization=SourceMaterialization.CAPTURED,
                        content=f"Observed task evidence {source.source_id}.",
                    ),
                )
            await contexts.repositories.sources.add(
                connection,
                SCOPE,
                ContentSource(
                    name=INTERNAL.source_id,
                    materialization=SourceMaterialization.CAPTURED,
                    content=INTERNAL_TEXT,
                    internal=ContentSourceInternal(
                        role="lineage_only",
                        operation="artifact_create",
                        target=ContentSourceTarget(scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=1),
                    ),
                ),
            )
            for ref, source in ((EA, A), (EB, B)):
                await contexts.repositories.artifacts.create(
                    connection,
                    SCOPE,
                    ref.artifact_id,
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation=f"Task {source.source_id} was requested.",
                            action=f"Executed task {source.source_id}.",
                            outcome=f"Observed task {source.source_id} completion.",
                            lesson=f"Preserve the exact evidence of task {source.source_id}.",
                        ),
                        sources=(source,),
                    ),
                )
            for revision, entries, sources in (
                (1, (alpha, beta), (A, B, INTERNAL)),
                (2, (revised, beta), (C,)),
            ):
                changes = [
                    {
                        "op": "add" if revision == 1 else "revise",
                        "entry_id": entry["entry_id"],
                        "from_entry_version_id": entry["previous_version_id"],
                        "to_entry_version_id": entry["entry_version_id"],
                        "reason": None,
                    }
                    for entry in entries
                    if entry["created_in_revision"] == revision
                ]
                content = {
                    "schema": "powercontext.memory.v1",
                    "manifest": {
                        "format": "flat-v1",
                        "entries": [
                            {key: entry[key] for key in ("entry_id", "entry_version_id", "entry_content_hash")}
                            | {"state": "active"}
                            for entry in entries
                        ],
                    },
                    "changes": changes,
                }
                # Immutable legacy bytes bypass current collection-write rejection.
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id=SCOPE,
                        family="memory",
                        artifact_id=COLLECTION,
                        revision=revision,
                        content=json.dumps(content).encode(),
                        memory_citations=None,
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_LINEAGE_SOURCES_TABLE),
                    [
                        {
                            "scope_id": SCOPE,
                            "family": "memory",
                            "artifact_id": COLLECTION,
                            "revision": revision,
                            "ordinal": ordinal,
                            "source_type": source.source_type,
                            "source_id": source.source_id,
                        }
                        for ordinal, source in enumerate(sources)
                    ],
                )
                if revision == 1:
                    await connection.execute(
                        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE),
                        [
                            {
                                "scope_id": SCOPE,
                                "family": "memory",
                                "artifact_id": COLLECTION,
                                "revision": revision,
                                "ordinal": ordinal,
                                "upstream_family": ref.family,
                                "upstream_artifact_id": ref.artifact_id,
                                "upstream_revision": ref.revision,
                            }
                            for ordinal, ref in enumerate((EA, EB))
                        ],
                    )
            await connection.execute(
                insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=2)
            )
            await connection.execute(
                insert(MEMORY_ENTRY_VERSIONS_TABLE),
                [
                    {"scope_id": SCOPE, "family": "memory", "memory_artifact_id": COLLECTION, **entry}
                    for entry in (alpha, beta, revised)
                ],
            )
            access = RelationalAccessRepository(contexts.database, connection=connection)
            for entry_id in ("alpha", "beta"):
                await access.establish_artifact_owner(
                    ArtifactOwnerRelation(
                        resource=ResourceRef.artifact(
                            SCOPE,
                            family="memory",
                            artifact_id=COLLECTION,
                            selector=MemoryEntrySelector(entry_id=entry_id),
                        ),
                        owner=PrincipalRef(type="service", id="migration-owner"),
                        established_at=datetime(2026, 1, 1, tzinfo=UTC),
                        policy_revision="pending",
                        idempotency_key=f"owner:{entry_id}",
                    )
                )
            if extra is not None:
                await extra(contexts, connection)
            internal_bytes = await connection.scalar(
                select(SOURCES_TABLE.c.payload).where(
                    SOURCES_TABLE.c.scope_id == SCOPE, SOURCES_TABLE.c.source_id == INTERNAL.source_id
                )
            )
            assert isinstance(internal_bytes, bytes)
    return internal_bytes


async def _migrate(config: SQLiteConfig, decisions=None):
    async with SQLiteProfile.open(config, tables=()) as profile:
        return await apply_atomic_memory_migration(
            profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True, decisions=decisions
        )


async def _seed_and_migrate(config: SQLiteConfig, extra=None) -> bytes:
    internal_bytes = await _seed(config, extra)
    result = await _migrate(config)
    assert result.ready, result.errors
    return internal_bytes


@pytest.fixture
def migrated(tmp_path: Path) -> tuple[SQLiteConfig, bytes]:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'migration-evidence.db'}")
    return config, asyncio.run(_seed_and_migrate(config))


async def _public_collection_rows(connection) -> list[Any]:
    statement = select(ARTIFACTS_TABLE.c.revision).where(
        ARTIFACTS_TABLE.c.scope_id == SCOPE, ARTIFACTS_TABLE.c.family == "memory"
    )
    return list((await connection.execute(statement)).scalars())


async def _archive_metadata(connection, revision: int) -> dict[str, Any]:
    value = await connection.scalar(
        select(ARCHIVE_TABLE.c.metadata).where(
            ARCHIVE_TABLE.c.scope_id == SCOPE,
            ARCHIVE_TABLE.c.artifact_id == COLLECTION,
            ARCHIVE_TABLE.c.revision == revision,
        )
    )
    return json.loads(value)


async def _restore_public_collection(connection) -> None:
    for row in (await connection.execute(select(ARCHIVE_TABLE))).mappings():
        await connection.execute(
            insert(ARTIFACTS_TABLE).values(
                scope_id=row["scope_id"],
                family=row["family"],
                artifact_id=row["artifact_id"],
                revision=row["revision"],
                content=row["content"],
                memory_citations=row["memory_citations"],
            )
        )
    await connection.execute(
        insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=2)
    )


def _resolver(contexts) -> EvidenceResolver:
    async def read_memory(connection, citation):
        service = MemoryService(
            backend=RelationalMemoryBackend(
                database=contexts.database,
                scope_id=SCOPE,
                artifacts=contexts.repositories.artifacts,
                connection=connection,
            )
        )
        return await service.validate_citation(citation)

    return EvidenceResolver(
        scope_id=SCOPE,
        sources=contexts.repositories.sources,
        artifacts=contexts.repositories.artifacts,
        memory_reader=read_memory,
    )


def _projected_sources(resolved) -> tuple[SourceRef, ...]:
    projected = {item.evidence_id for item in resolved.projection.evidence if item.kind == "source"}
    return tuple(
        sorted(
            (node.source for node in resolved.manifest.nodes if node.evidence_id in projected),
            key=lambda source: source.model_dump_json(),
        )
    )


def _projected_artifacts(resolved) -> tuple[ArtifactRef, ...]:
    projected = {item.evidence_id for item in resolved.projection.evidence}
    return tuple(
        node.artifact for node in resolved.manifest.nodes if node.evidence_id in projected and node.artifact is not None
    )


async def _imported_snapshot(connection):
    identities = tuple(
        (ref.artifact_id, ref.revision) for ref in (_atomic("alpha", 1), _atomic("alpha", 2), _atomic("beta", 1))
    )
    snapshot = {}
    for table in (ARTIFACTS_TABLE, ARTIFACT_LINEAGE_SOURCES_TABLE, ARTIFACT_LINEAGE_ARTIFACTS_TABLE):
        statement = (
            select(table)
            .where(
                table.c.scope_id == SCOPE,
                table.c.family == "atomic-memory",
                tuple_(table.c.artifact_id, table.c.revision).in_(identities),
            )
            .order_by(table.c.artifact_id, table.c.revision)
        )
        if "ordinal" in table.c:
            statement = statement.order_by(table.c.ordinal)
        snapshot[table.name] = tuple(tuple(row) for row in (await connection.execute(statement)).all())
    return snapshot


@pytest.mark.parametrize(
    ("entry_id", "revision", "expected"), [("alpha", 1, (A,)), ("beta", 1, (B,)), ("alpha", 2, (A, C))]
)
def test_migrated_entry_projects_only_its_exact_sources(migrated, entry_id, revision, expected) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            selected = _atomic(entry_id, revision)
            resolved = await _resolver(contexts).resolve(connection, artifacts=(selected,))
            assert _projected_sources(resolved) == expected
            own, unrelated = (EA, EB) if entry_id == "alpha" else (EB, EA)
            assert own in _projected_artifacts(resolved)
            assert unrelated not in _projected_artifacts(resolved)
            stored = await contexts.repositories.artifacts.get(connection, SCOPE, selected)
            assert stored.lineage.artifacts == (own,)
            assert all(source in stored.lineage.sources for source in expected)

    asyncio.run(scenario())


def test_legacy_entry_write_source_retains_target_and_stays_out_of_projection(migrated) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            stored = (await contexts.repositories.sources.get(connection, SCOPE, INTERNAL)).value
            assert isinstance(stored, ContentSource) and stored.internal is not None
            assert stored.internal.target == ContentSourceTarget(
                scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=1
            )
            payload = await connection.scalar(
                select(SOURCES_TABLE.c.payload).where(
                    SOURCES_TABLE.c.scope_id == SCOPE, SOURCES_TABLE.c.source_id == INTERNAL.source_id
                )
            )
            assert payload == migrated[1]
            resolved = await _resolver(contexts).resolve(connection, artifacts=(_atomic("alpha", 2),))
            receipt = next(node for node in resolved.manifest.nodes if node.source == INTERNAL)
            assert receipt.role == "lineage_only"
            assert all(item.evidence_id != receipt.evidence_id for item in resolved.projection.evidence)
            assert all(INTERNAL_TEXT not in item.text for item in resolved.projection.evidence)

    asyncio.run(scenario())


def test_merge_keeps_migrated_input_history_without_unrelated_collection_sources(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            alpha = await memory.get(_atomic("alpha", 2).artifact_id)
            (created,) = await contexts.records.create_atomic_memories(
                SCOPE, ({"kind": "fact", "text": "Another standalone fact."},)
            )
            other = await memory.get(created.artifact_id)
            merged = await memory.merge(
                (alpha.as_read(), other.as_read()), AtomicMemoryContent(kind="fact", text="Merged task evidence.")
            )
            async with contexts.database.transaction() as connection:
                resolved = await _resolver(contexts).resolve(connection, artifacts=(merged.primary.ref,))
                assert _projected_sources(resolved) == (A, C)
                assert any(node.artifact == alpha.ref and node.historical for node in resolved.manifest.nodes)

    asyncio.run(scenario())


def test_restoration_of_migrated_history_does_not_add_unrelated_sources(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            async with contexts.database.transaction() as connection:
                imported = await _imported_snapshot(connection)
            restored = await contexts.atomic_memory.for_scope(SCOPE).restore(
                _atomic("alpha", 2).artifact_id, revision=1
            )
            assert restored.primary.artifact.content.text == "Task A was completed."
            async with contexts.database.transaction() as connection:
                resolved = await _resolver(contexts).resolve(connection, artifacts=(restored.primary.ref,))
                # Restoration retains both exact current and selected historical revisions.
                assert _projected_sources(resolved) == (A, C)
                assert any(node.artifact == _atomic("alpha", 1) for node in resolved.manifest.nodes)
                report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
                assert report.ready, report.errors
                assert await _imported_snapshot(connection) == imported
            repeated = await apply_atomic_memory_migration(
                contexts.database, contexts.atomic_memory.index, maintenance_confirmed=True
            )
            assert repeated.ready, repeated.errors
            async with contexts.database.transaction() as connection:
                assert await _imported_snapshot(connection) == imported

    asyncio.run(scenario())


def test_apply_replaces_the_collection_anchor_left_by_an_earlier_import(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            target = _atomic("alpha", 2)
            identity = {"scope_id": SCOPE, "family": "atomic-memory", "artifact_id": target.artifact_id, "revision": 2}
            async with contexts.database.transaction() as connection:
                direct = await _imported_snapshot(connection)
                # An interrupted earlier run left the collection online with anchored imports.
                await _restore_public_collection(connection)
                for table in (ARTIFACT_LINEAGE_SOURCES_TABLE, ARTIFACT_LINEAGE_ARTIFACTS_TABLE):
                    await connection.execute(
                        table.delete().where(*(table.c[key] == value for key, value in identity.items()))
                    )
                for ordinal, ref in enumerate((_collection(2), EA, _atomic("alpha", 1))):
                    await connection.execute(
                        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
                            **identity,
                            ordinal=ordinal,
                            upstream_family=ref.family,
                            upstream_artifact_id=ref.artifact_id,
                            upstream_revision=ref.revision,
                        )
                    )
                report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
                assert not report.ready
                assert any("collection anchor" in error for error in report.errors), report.errors
            repaired = await apply_atomic_memory_migration(
                contexts.database, contexts.atomic_memory.index, maintenance_confirmed=True
            )
            assert repaired.ready, repaired.errors
            async with contexts.database.transaction() as connection:
                assert await _imported_snapshot(connection) == direct
                assert not await _public_collection_rows(connection)

    asyncio.run(scenario())


def test_undo_merge_preserves_each_migrated_entry_evidence(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            alpha = await memory.get(_atomic("alpha", 2).artifact_id)
            beta = await memory.get(_atomic("beta", 1).artifact_id)
            merged = await memory.merge(
                (alpha.as_read(), beta.as_read()), AtomicMemoryContent(kind="fact", text="Both task facts.")
            )
            await memory.restore(merged.primary.ref.artifact_id, operation="undo_merge")
            async with contexts.database.transaction() as connection:
                for original, expected in ((alpha, (A, C)), (beta, (B,))):
                    current = await memory.get(original.ref.artifact_id)
                    assert current.state.state == "active"
                    resolved = await _resolver(contexts).resolve(connection, artifacts=(current.ref,))
                    assert _projected_sources(resolved) == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("other_origin", ["atomic", "experience"])
def test_other_origin_does_not_broaden_migrated_entry_root_groups_or_selection(migrated, other_origin) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            other = _atomic("beta", 1)
            if other_origin == "experience":
                experience = await contexts.repositories.artifacts.create(
                    connection,
                    SCOPE,
                    "whole-collection-experience",
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation="Two tasks completed.",
                            action="Reviewed the task collection.",
                            outcome="Both results were recorded.",
                            lesson="Keep both task sources for this collection-wide judgment.",
                        ),
                        sources=(B,),
                        artifacts=(other,),
                    ),
                )
                other = experience.as_ref()
            selected = _atomic("alpha", 1)
            resolved = await _resolver(contexts).resolve(connection, artifacts=(selected, other))
            assert _projected_sources(resolved) == (A, B)
            alpha = next(item for item in resolved.projection.evidence if item.evidence_id == evidence_id(selected))
            groups = {group.group_id: group for group in resolved.projection.root_groups}
            assert tuple(source for group_id in alpha.root_group_ids for source in groups[group_id].sources) == (A,)
            chosen = select_evidence(resolved.manifest, (alpha.evidence_id,), skill=False, target=None)
            assert chosen.sources == (A,)
            assert chosen.artifacts == (selected,)

    asyncio.run(scenario())


def test_migrated_collection_leaves_public_artifact_reads(migrated) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            assert not await _public_collection_rows(connection)
            with pytest.raises(RepositoryNotFoundError):
                await contexts.repositories.artifacts.get(connection, SCOPE, _collection(2))

    asyncio.run(scenario())


class _TriggerExtractor:
    async def generate(self, request):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="fact", text="Tasks A and C were completed.", evidence_ids=(item.evidence_id,)
                    )
                    for item in request.evidence
                    if item.source_ref.source_id == "migration-trigger"
                )
            )
        )


class _CaptureReconciler:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        related = next(
            (
                item
                for item in request.related
                if any(original.ref == _atomic("alpha", 2) for original in item.original_refs)
            ),
            None,
        )
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create" if related is None else "noop",
                compared_ids=tuple(item.item_id for item in request.related),
                target_ids=() if related is None else (related.item_id,),
                content=(
                    AtomicMemoryReconciliationContent(kind="fact", text=request.proposal.text)
                    if related is None
                    else None
                ),
                evidence_ids=request.proposal.evidence_ids if related is None else (),
                reason="Retain the exact existing fact without altering its historical evidence.",
            )
        )


def test_source_flush_supplies_the_migrated_related_entry_current_content(migrated) -> None:
    reconciler = _CaptureReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_TriggerExtractor(), reconciler=reconciler, estimator=character_token_estimator()
    )

    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=migrated[0], runtime=RuntimeConfig(atomic_memory_related_mode="fts")),
            candidate_pipeline=pipeline,
        ) as contexts:
            context = await contexts.get(SCOPE)
            await contexts.records.capture_source(SCOPE, "content", "migration-trigger", "Recheck task A and C.", {})
            await context.triggers.flush(limit=20)
            observed = []
            for request in reconciler.requests:
                evidence = {item.evidence_id: item for item in request.evidence}
                for related in request.related:
                    if any(original.ref == _atomic("alpha", 2) for original in related.original_refs):
                        observed.append(tuple(evidence[key] for key in related.evidence_ids))
            assert observed, "The public flush must compare the imported alpha identity."
            for (published,) in observed:
                assert published.artifact_ref == _atomic("alpha", 2)
                assert published.content.text == "Tasks A and C were completed."

    asyncio.run(scenario())


ALPHA_2 = MemoryCitation(memory_ref=_collection(2), entry_id="alpha", entry_version_id="alpha-v2")
OUTCOME = SourceRef(source_type="content", source_id="legacy-outcome")
DREAM_REQUEST = CreateDreamRunRequest(
    operation="refine_experience", memory_citations=(ALPHA_2,), idempotency_key="legacy-dream"
)


def _experience(name: str) -> ExperienceContent:
    return ExperienceContent(
        situation=f"{name} was requested.",
        action=f"Executed {name}.",
        outcome=f"Observed {name} completion.",
        lesson=f"Preserve the exact evidence of {name}.",
    )


def _outcome() -> TaskOutcome:
    return TaskOutcome(
        objective="Ship task A.",
        status="failed",
        summary="Deployment failed.",
        observations=(
            WorkClaim(
                text="Deploy failed.", basis="verified", evidence=(HandoffMemoryCitation(memory_citation=ALPHA_2),)
            ),
        ),
    )


async def _candidate(connection, candidate_id: str, *, artifact_refs: list[Any], citations: list[Any] | None) -> None:
    await connection.execute(
        insert(ARTIFACT_CANDIDATE_VERSIONS_TABLE).values(
            scope_id=SCOPE,
            candidate_id=candidate_id,
            version=1,
            family="experience",
            proposal=_experience(candidate_id).model_dump_json(by_alias=True).encode(),
            source_refs=b"[]",
            artifact_refs=json.dumps(artifact_refs).encode(),
            memory_citations=None if citations is None else json.dumps(citations).encode(),
        )
    )
    await connection.execute(
        insert(ARTIFACT_CANDIDATE_HEADS_TABLE).values(
            scope_id=SCOPE, candidate_id=candidate_id, family="experience", version=1, status="pending"
        )
    )


async def _scope_row(connection) -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=SCOPE,
            title="Migration evidence",
            summary="Legacy reference fixture.",
            scope_id_search=SCOPE,
            title_search="migration evidence",
            summary_search="legacy reference fixture",
            version=1,
        )
    )


async def _legacy_references(contexts, connection) -> None:
    await contexts.repositories.artifacts.create(
        connection,
        SCOPE,
        "cited-experience",
        ExperienceDraft(content=_experience("cited"), sources=(A,), memory_citations=(ALPHA_2,)),
    )
    await contexts.repositories.artifacts.create(
        connection,
        SCOPE,
        "collection-experience",
        ExperienceDraft(content=_experience("collection"), sources=(B,), artifacts=(_collection(1),)),
    )
    await _candidate(connection, "cited-candidate", artifact_refs=[], citations=[ALPHA_2.model_dump(mode="json")])
    handoff = HandoffContent(
        objective="Continue task A.",
        state=(HandoffStatement(text="Task A shipped.", citations=(HandoffMemoryCitation(memory_citation=ALPHA_2),)),),
        disposition="complete",
    )
    await connection.execute(
        insert(ARTIFACTS_TABLE).values(
            scope_id=SCOPE,
            family="handoff",
            artifact_id="legacy-handoff",
            revision=1,
            content=handoff.model_dump_json(by_alias=True).encode(),
            memory_citations=None,
        )
    )
    await connection.execute(
        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
            scope_id=SCOPE,
            family="handoff",
            artifact_id="legacy-handoff",
            revision=1,
            ordinal=0,
            upstream_family="memory",
            upstream_artifact_id=COLLECTION,
            upstream_revision=2,
        )
    )
    await connection.execute(
        insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="handoff", artifact_id="legacy-handoff", revision=1)
    )
    await _scope_row(connection)
    outcome = _outcome()
    stored = await contexts.repositories.sources.add(
        connection,
        SCOPE,
        ContentSource(
            name=OUTCOME.source_id,
            materialization=SourceMaterialization.CAPTURED,
            content=outcome.model_dump_json(by_alias=True, exclude_none=False, indent=2),
            metadata={"kind": "task-outcome", "schema": "powercontext.task-outcome.v1"},
        ),
    )
    await RecurrenceRepository().append_match(
        connection,
        RecurrenceMatch(
            scope_id=SCOPE,
            task_outcome_ref=OUTCOME,
            task_outcome_position=stored.journal_position,
            failure_ref=TaskOutcomeItemRef(
                task_outcome_ref=OUTCOME,
                item_kind="observation",
                item_index=0,
                item_digest=item_digest(outcome.observations[0]),
            ),
            candidate_set_mode="scope_heads",
            candidate_refs=(),
            candidate_set_digest=candidate_set_digest(()),
            result="unmatched",
        ),
    )
    await DreamRepository().create(
        connection,
        DreamRecord(
            run=DreamRun(
                scope_id=SCOPE,
                run_id="legacy-dream",
                operation="refine_experience",
                status="succeeded",
                accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
            request=DREAM_REQUEST,
            principal_id="migration-owner",
        ),
    )


def test_migration_converts_exact_citations_and_archives_collection_relationships(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'references.db'}")
    alpha = _atomic("alpha", 2)

    async def scenario() -> None:
        await _seed_and_migrate(config, _legacy_references)
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            assert not await _public_collection_rows(connection)
            report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
            assert report.ready, report.errors

            cited = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="experience", artifact_id="cited-experience", revision=1)
            )
            assert cited.lineage.memory_citations == ()
            assert cited.lineage.artifacts == (alpha,)

            whole = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="experience", artifact_id="collection-experience", revision=1)
            )
            assert whole.lineage.artifacts == ()
            (incoming,) = (await _archive_metadata(connection, 1))["incoming_references"]
            assert incoming["referrer"]["artifact_id"] == "collection-experience"
            assert incoming["value"] == _collection(1).model_dump(mode="json")

            candidate = (
                await connection.execute(
                    select(
                        ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.artifact_refs,
                        ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.memory_citations,
                    ).where(ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.candidate_id == "cited-candidate")
                )
            ).one()
            assert json.loads(candidate.artifact_refs) == [alpha.model_dump(mode="json")]
            assert json.loads(candidate.memory_citations) == []

            handoff = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="handoff", artifact_id="legacy-handoff", revision=1)
            )
            assert handoff.content.state[0].citations == (HandoffArtifactCitation(artifact_ref=alpha),)
            assert handoff.lineage.artifacts == (alpha,)

            source = (await contexts.repositories.sources.get(connection, SCOPE, OUTCOME)).value
            assert isinstance(source, ContentSource)
            converted = TaskOutcome.model_validate_json(source.content)
            assert converted.observations[0].evidence == (HandoffArtifactCitation(artifact_ref=alpha),)
            match = await RecurrenceRepository().find_match(
                connection,
                SCOPE,
                OUTCOME,
                TaskOutcomeItemRef(
                    task_outcome_ref=OUTCOME,
                    item_kind="observation",
                    item_index=0,
                    item_digest=item_digest(converted.observations[0]),
                ),
            )
            assert match is not None
            assert match.failure_ref.item_digest == item_digest(converted.observations[0])

            dream = await DreamRepository().get(connection, SCOPE, "legacy-dream")
            assert dream.request is None and dream.run.input_manifest is None
            history = dream.run.historical_data
            assert history is not None and isinstance(history["request"], dict)
            assert history["request"]["memory_citations"] == [ALPHA_2.model_dump(mode="json")]
            replay = await DreamRepository().find_request(connection, SCOPE, "migration-owner", DREAM_REQUEST)
            assert replay is not None and replay.run.run_id == "legacy-dream"

    asyncio.run(scenario())


def test_candidate_left_without_evidence_blocks_until_an_explicit_replacement(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'blocked.db'}")

    async def blocked(_contexts, connection) -> None:
        await _candidate(
            connection, "collection-candidate", artifact_refs=[_collection(1).model_dump(mode="json")], citations=None
        )

    async def scenario() -> None:
        await _seed(config, blocked)
        refused = await _migrate(config)
        assert not refused.ready
        assert any("collection-candidate" in error and "replace decision" in error for error in refused.errors)
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.transaction() as connection:
            assert await _public_collection_rows(connection) == [1, 2]
        decisions = load_decisions({
            "format": DECISIONS_FORMAT,
            "decisions": [
                {
                    "carrier": "candidate",
                    "scope_id": SCOPE,
                    "candidate_id": "collection-candidate",
                    "version": 1,
                    "field": "artifact_refs",
                    "action": "replace",
                    "artifact_refs": [EA.model_dump(mode="json")],
                }
            ],
        })
        applied = await _migrate(config, decisions)
        assert applied.ready, applied.errors
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.transaction() as connection:
            refs = await connection.scalar(
                select(ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.artifact_refs).where(
                    ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.candidate_id == "collection-candidate"
                )
            )
            assert isinstance(refs, bytes) and json.loads(refs) == [EA.model_dump(mode="json")]
            (incoming,) = (await _archive_metadata(connection, 1))["incoming_references"]
            assert incoming["carrier"] == "candidate" and incoming["decision"] == "replace"
        rerun = await _migrate(config, decisions)
        assert rerun.ready, rerun.errors

    asyncio.run(scenario())


def _insert_handoff(connection, artifact_id: str, content: HandoffContent):
    return connection.execute(
        insert(ARTIFACTS_TABLE).values(
            scope_id=SCOPE,
            family="handoff",
            artifact_id=artifact_id,
            revision=1,
            content=content.model_dump_json(by_alias=True).encode(),
            memory_citations=None,
        )
    )


def test_handoff_collection_citation_is_archived_and_removed(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff-collection.db'}")

    async def cited(_contexts, connection) -> None:
        statement = HandoffStatement(
            text="Task A shipped.",
            citations=(HandoffArtifactCitation(artifact_ref=_collection(2)), HandoffSourceCitation(source_ref=A)),
        )
        await _insert_handoff(
            connection,
            "mixed-handoff",
            HandoffContent(objective="Continue.", state=(statement,), disposition="complete"),
        )

    async def scenario() -> None:
        await _seed_and_migrate(config, cited)
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            handoff = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="handoff", artifact_id="mixed-handoff", revision=1)
            )
            assert handoff.content.state[0].citations == (HandoffSourceCitation(source_ref=A),)
            (incoming,) = (await _archive_metadata(connection, 2))["incoming_references"]
            assert incoming["carrier"] == "handoff" and incoming["path"] == "/state/0/citations/0"
            report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
            assert report.ready, report.errors

    asyncio.run(scenario())


def test_handoff_supported_only_by_a_collection_blocks_before_any_change(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff-only-collection.db'}")

    async def cited(_contexts, connection) -> None:
        statement = HandoffStatement(
            text="Task A shipped.", citations=(HandoffArtifactCitation(artifact_ref=_collection(2)),)
        )
        await _insert_handoff(
            connection,
            "orphan-handoff",
            HandoffContent(objective="Continue.", state=(statement,), disposition="complete"),
        )

    async def scenario() -> None:
        await _seed(config, cited)
        refused = await _migrate(config)
        assert not refused.ready
        assert any("orphan-handoff" in error for error in refused.errors), refused.errors
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.transaction() as connection:
            assert await _public_collection_rows(connection) == [1, 2]

    asyncio.run(scenario())


def test_collections_citing_each_other_are_removed_regardless_of_identifier_order(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'collection-chain.db'}")
    cited = "a-cited-memory"

    async def chain(_contexts, connection) -> None:
        empty = {"schema": "powercontext.memory.v1", "manifest": {"format": "flat-v1", "entries": []}, "changes": []}
        await connection.execute(
            insert(ARTIFACTS_TABLE).values(
                scope_id=SCOPE,
                family="memory",
                artifact_id=cited,
                revision=1,
                content=json.dumps(empty).encode(),
                memory_citations=None,
            )
        )
        await connection.execute(
            insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="memory", artifact_id=cited, revision=1)
        )
        await connection.execute(
            insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
                scope_id=SCOPE,
                family="memory",
                artifact_id=COLLECTION,
                revision=1,
                ordinal=2,
                upstream_family="memory",
                upstream_artifact_id=cited,
                upstream_revision=1,
            )
        )

    async def scenario() -> None:
        await _seed_and_migrate(config, chain)
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.transaction() as connection:
            assert not await _public_collection_rows(connection)
            lineage = (await _archive_metadata(connection, 1))["lineage"]["artifacts"]
            assert {row["upstream_artifact_id"] for row in lineage} == {EA.artifact_id, EB.artifact_id, cited}

    asyncio.run(scenario())


def test_unfinished_dream_pinning_a_rewritten_source_blocks_migration(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'unfinished-dream.db'}")

    async def pinned(contexts, connection) -> None:
        await _scope_row(connection)
        await contexts.repositories.sources.add(
            connection,
            SCOPE,
            ContentSource(
                name=OUTCOME.source_id,
                materialization=SourceMaterialization.CAPTURED,
                content=_outcome().model_dump_json(by_alias=True, exclude_none=False, indent=2),
                metadata={"kind": "task-outcome", "schema": "powercontext.task-outcome.v1"},
            ),
        )
        manifest = EvidenceManifest(
            artifacts=(EA,),
            sources=(OUTCOME,),
            nodes=(EvidenceNode(evidence_id="outcome", kind="source", digest="0" * 64, source=OUTCOME, role="root"),),
            projection_digest="0" * 64,
            projection_bytes=0,
        )
        await DreamRepository().create(
            connection,
            DreamRecord(
                run=DreamRun(
                    scope_id=SCOPE,
                    run_id="pending-dream",
                    operation="refine_experience",
                    status="running",
                    accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
                    input_manifest=manifest,
                ),
                request=CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(EA,), idempotency_key="pending-dream"
                ),
                principal_id="migration-owner",
            ),
        )

    async def scenario() -> None:
        await _seed(config, pinned)
        refused = await _migrate(config)
        assert not refused.ready
        assert any("pending-dream" in error and "finish" in error for error in refused.errors), refused.errors

    asyncio.run(scenario())


def test_runtime_start_does_not_read_the_archive_or_legacy_entry_tables(migrated) -> None:
    config, _internal = migrated

    async def scenario() -> None:
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.transaction() as connection:
            await connection.execute(text(f"DROP TABLE {ARCHIVE_TABLE.name}"))
            await connection.execute(text(f"DROP TABLE {MEMORY_ENTRY_VERSIONS_TABLE.name}"))
        async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
            record = await contexts.atomic_memory.for_scope(SCOPE).get(_atomic("alpha", 2).artifact_id)
            assert record.artifact.as_ref() == _atomic("alpha", 2)

    asyncio.run(scenario())


def test_development_projection_layout_is_recreated_and_rebuilt_after_apply(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'development-projection.db'}")

    async def development_layout(_contexts, connection) -> None:
        await connection.execute(text("DROP TABLE pc_atomic_memory_current"))
        await connection.execute(text("DROP TABLE IF EXISTS pc_atomic_memory_current_fts"))
        await connection.execute(
            text(
                "CREATE TABLE pc_atomic_memory_current (scope_id VARCHAR NOT NULL, artifact_id VARCHAR NOT NULL, "
                "owner_type VARCHAR NOT NULL, owner_id VARCHAR NOT NULL, PRIMARY KEY (scope_id, artifact_id))"
            )
        )

    async def scenario() -> None:
        await _seed(config, development_layout)
        applied = await _migrate(config)
        assert not applied.ready
        assert len(applied.errors) == 1 and "atomic-memory-rebuild-projection" in applied.errors[0]
        async with SQLiteProfile.open(config, tables=()) as profile:
            rebuilt = await rebuild_atomic_memory_projection(
                profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True
            )
            assert rebuilt.ready, rebuilt.errors
            async with profile.database.transaction() as connection:
                report = await verify_atomic_memory_migration(connection, index=SQLiteAtomicMemoryIndex())
                assert report.ready, report.errors

    asyncio.run(scenario())
