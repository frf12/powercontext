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

"""Source-driven reconciliation publishes a merge and preserves its exact inputs."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.sources import SourceRef
from tests.e2e.dream_support import memory_source_text

SCOPE = "project"
CANARY = "Database rollout uses canary deployments."
PAUSE = "Database rollout pauses when error rate increases."
CONSOLIDATION = "Keep the database rollout canary and error rate rules together as one deployment policy."
POLICY = "Database rollout uses canary deployments and pauses when error rate increases."


class _PolicyExtractor:
    async def generate(self, request: AtomicMemoryExtractionInput, /):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="constraint",
                        text=POLICY if text == CONSOLIDATION else text,
                        evidence_ids=(item.evidence_id,),
                    )
                    for item in request.evidence
                    if (text := memory_source_text(item)) is not None
                )
            )
        )


class _PolicyReconciler:
    def __init__(self) -> None:
        self.requests: list[AtomicMemoryReconciliationInput] = []

    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        self.requests.append(request)
        inputs = tuple(item for item in request.related if item.text in {CANARY, PAUSE})
        merge = request.proposal.text == POLICY and {item.text for item in inputs} == {CANARY, PAUSE}
        evidence_ids = tuple(
            dict.fromkeys(
                identifier
                for item in (request.proposal, *inputs)
                if merge or item is request.proposal
                for identifier in item.evidence_ids
            )
        )
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="merge" if merge else "create",
                compared_ids=tuple(item.item_id for item in request.related),
                target_ids=tuple(item.item_id for item in inputs) if merge else (),
                content=AtomicMemoryContent(kind=request.proposal.kind, text=request.proposal.text),
                evidence_ids=evidence_ids,
                reason="Combine the two existing rollout rules with the consolidation Source evidence."
                if merge
                else "Retain an independent rollout rule with its Source evidence.",
            )
        )


def test_source_flush_automatically_merges_existing_memories_and_preserves_history(tmp_path: Path) -> None:
    reconciler = _PolicyReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_PolicyExtractor(), reconciler=reconciler, estimator=character_token_estimator()
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'generation-merge.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            originals = []
            for position, (source_id, text) in enumerate((("canary-rule", CANARY), ("pause-rule", PAUSE)), 1):
                source = await contexts.records.capture_source(SCOPE, "content", source_id, text, {})
                assert source.position == position
                flush = await context.triggers.flush(limit=1)
                assert flush.previous_cursor == position - 1
                assert flush.current_cursor == position
                original = next(item for item in (await memory.list()).items if item.artifact.content.text == text)
                assert original.state.state == "active"
                assert original.artifact.lineage.sources == (SourceRef(source_type="content", source_id=source_id),)
                originals.append(original)
            assert len((await memory.list()).items) == 2

            source = await contexts.records.capture_source(SCOPE, "content", "consolidate-rules", CONSOLIDATION, {})
            source_ref = SourceRef(source_type="content", source_id=source.source_id)
            result = await context.triggers.flush(limit=1)
            assert result.previous_cursor == 2
            assert result.current_cursor == result.high_watermark == source.position == 3
            assert result.source_count == 1
            assert result.processed and not result.remaining_work
            assert (await context.triggers.cursor()).sequence == 3

            (merged,) = (await memory.list()).items
            assert merged.ref.artifact_id not in {item.ref.artifact_id for item in originals}
            assert merged.ref.revision == 1
            assert merged.state.state == "active" and merged.state.merged_into_id is None
            assert merged.artifact.content.text == POLICY
            assert merged.artifact.content.creation is not None
            assert set(merged.artifact.content.creation.input_artifact_ids) == {
                item.ref.artifact_id for item in originals
            }
            assert merged.artifact.lineage.sources == (source_ref,)
            assert len(merged.artifact.lineage.artifacts) == len(originals)
            assert all(item.ref in merged.artifact.lineage.artifacts for item in originals)

            (comparison,) = tuple(request for request in reconciler.requests if request.proposal.text == POLICY)
            recalled = tuple(read.ref for item in comparison.related for read in item.original_refs)
            assert len(recalled) == len(originals)
            assert all(item.ref in recalled for item in originals)
            evidence = {item.evidence_id: item for item in comparison.evidence}
            assert tuple(evidence[key].source_ref for key in comparison.proposal.evidence_ids) == (source_ref,)
            assert tuple(memory_source_text(evidence[key]) for key in comparison.proposal.evidence_ids) == (
                CONSOLIDATION,
            )
            for original in originals:
                (related,) = tuple(item for item in comparison.related if original.as_read() in item.original_refs)
                assert (
                    tuple(evidence[key].source_ref for key in related.evidence_ids) == original.artifact.lineage.sources
                )
                assert tuple(evidence[key].via_artifact for key in related.evidence_ids) == (original.ref,)
                current = await memory.get(original.ref.artifact_id)
                assert current.state.state == "merged"
                assert current.state.merged_into_id == merged.ref.artifact_id
                assert current.artifact == original.artifact
                assert (
                    await memory.get(original.ref.artifact_id, revision=original.ref.revision)
                ).artifact == original.artifact
                historical = await contexts.records.get_artifact_revision(
                    SCOPE, "atomic-memory", original.ref.artifact_id, original.ref.revision
                )
                assert historical.content["text"] == original.artifact.content.text
                assert historical.sources == original.artifact.lineage.sources

            assert tuple(hit.hit.artifact_ref for hit in (await memory.search("Database rollout")).hits) == (
                merged.ref,
            )
            snapshot = (await memory.list(states=("active", "merged"))).items
            assert len(snapshot) == len(originals) + 1
            assert all(record.ref in tuple(item.ref for item in snapshot) for record in (merged, *originals))
            idle = await context.triggers.flush(limit=1)
            assert idle.previous_cursor == idle.current_cursor == idle.high_watermark == 3
            assert idle.source_count == 0 and not idle.processed
            assert (await memory.list(states=("active", "merged"))).items == snapshot

        # A new runtime sees the committed merge, frozen inputs and consumed cursor.
        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            assert (await memory.list()).items == (merged,)
            assert (await memory.list(states=("active", "merged"))).items == snapshot
            assert (await context.triggers.cursor()).sequence == 3
            assert not (await context.triggers.flush(limit=1)).processed
            assert tuple(hit.hit.artifact_ref for hit in (await memory.search("Database rollout")).hits) == (
                merged.ref,
            )

    asyncio.run(scenario())


def test_failed_merge_projection_rolls_back_entire_source_window_and_recomputes(tmp_path: Path, monkeypatch) -> None:
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_PolicyExtractor(), reconciler=_PolicyReconciler(), estimator=character_token_estimator()
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'merge-rollback.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get(SCOPE)
            application = contexts.atomic_memory
            memory = application.for_scope(SCOPE)
            for source_id, text in (("canary-rule", CANARY), ("pause-rule", PAUSE)):
                await contexts.records.capture_source(SCOPE, "content", source_id, text, {})
                await context.triggers.flush(limit=1)
            originals = (await memory.list()).items
            await contexts.records.capture_source(SCOPE, "content", "consolidate-rules", CONSOLIDATION, {})
            remove = application.publisher.remove

            async def fail_projection(connection, scope_id, artifact_id):
                await remove(connection, scope_id, artifact_id)
                if artifact_id == originals[-1].ref.artifact_id:
                    raise RuntimeError("projection publication failed")  # noqa: TRY003

            with monkeypatch.context() as patch:
                patch.setattr(application.publisher, "remove", fail_projection)
                with pytest.raises(RuntimeError, match="projection publication failed"):
                    await context.triggers.flush(limit=1)
            assert (await context.triggers.cursor()).sequence == 2
            assert (await memory.list(states=("active", "forgotten", "merged", "retired"))).items == originals
            assert {
                (hit.hit.artifact_ref.artifact_id, hit.hit.artifact_ref.revision)
                for hit in (await memory.search("Database rollout")).hits
            } == {(original.ref.artifact_id, original.ref.revision) for original in originals}
            retry = await context.triggers.flush(limit=1)
            assert retry.previous_cursor == 2 and retry.current_cursor == 3
            (result,) = (await memory.list()).items
            assert result.artifact.content.text == POLICY
            assert result.artifact.lineage.sources == (SourceRef(source_type="content", source_id="consolidate-rules"),)
            for original in originals:
                frozen = await memory.get(original.ref.artifact_id)
                assert frozen.state.merged_into_id == result.ref.artifact_id
                assert frozen.artifact == original.artifact

    asyncio.run(scenario())
