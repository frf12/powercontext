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

"""Runtime operations over the Atomic Memory Family service and current index."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from time import perf_counter
from typing import cast
from uuid import uuid4

from sqlalchemy import insert, select

from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError, AtomicMemoryPreviewStaleError
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemoryContent,
    AtomicMemoryRead,
    AtomicMemoryRecord,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.restoration import AtomicMemoryPreviewSigner
from powercontext.builtin.artifacts.atomic_memory.service import AtomicMemoryService
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding, normalize_query
from powercontext.builtin.artifacts.memory.models import MemoryQueryEmbedding
from powercontext.builtin.artifacts.memory.reranking import MemoryReranker
from powercontext.builtin.artifacts.search import AdmissionCounts, AdmissionFloor
from powercontext.builtin.inference import InferenceUsage, InvalidInferenceOutputError
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndex,
    AtomicMemoryIndexError,
    AtomicMemoryIndexHit,
    AtomicMemoryProjectionPublisher,
    AtomicMemorySearchMode,
    AtomicMemorySearchRequest,
    combine_atomic_memory_channels,
)
from powercontext.builtin.persistence.atomic_memory_schema import ATOMIC_MEMORY_STATES_TABLE
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACT_TAGS_TABLE
from powercontext.builtin.persistence.tags import tag_predicate
from powercontext.builtin.records import BaseOperationNotSupportedError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.atomic_memory_security import (
    AtomicMemoryExecutionContext,
    AtomicMemorySecurity,
    load_atomic_memory_security,
    load_atomic_memory_tags,
)
from powercontext.builtin.tags import TagFilter, normalize_tags


@dataclass(frozen=True, slots=True)
class AtomicMemoryPage:
    items: tuple[AtomicMemoryRecord, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AtomicMemorySearchHit:
    hit: AtomicMemoryIndexHit
    matched_by: tuple[str, ...]

    @property
    def text(self) -> str:
        return self.hit.text


@dataclass(frozen=True, slots=True)
class AtomicMemoryRerankTrace:
    policy_id: str
    candidate_hits: tuple[AtomicMemorySearchHit, ...]
    selected_ranks: tuple[int, ...]
    discarded_rank_count: int
    used_fallback: bool
    latency_ms: float
    usage: InferenceUsage


@dataclass(frozen=True, slots=True)
class AtomicMemorySearchPage:
    mode: str
    hits: tuple[AtomicMemorySearchHit, ...]
    query_embedding: MemoryQueryEmbedding | None = None
    embedding_calls: int = 0
    generation_calls: int = 0
    admission: AdmissionCounts | None = None
    rerank: AtomicMemoryRerankTrace | None = None


class AtomicMemoryApplication:
    def __init__(
        self,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        index: AtomicMemoryIndex,
        *,
        default_context: AtomicMemoryExecutionContext,
        embedding_model=None,
        cursor_secret: bytes | None = None,
        id_factory=None,
        restore_retry_budget: int = 3,
        preview_signer: AtomicMemoryPreviewSigner | None = None,
        reranker: MemoryReranker | None = None,
        rerank_candidate_limit: int = 30,
        prompt_context_factory=None,
    ) -> None:
        if reranker is not None and not getattr(reranker, "supports_atomic_memory", False):
            raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "Atomic text candidate contract")
        self.reranker = reranker
        self.rerank_candidate_limit = rerank_candidate_limit
        self.prompt_context_factory = prompt_context_factory
        self.database = database
        self.artifacts = artifacts
        self.index = index
        self.security = AtomicMemorySecurity(database)
        self.default_context = default_context
        self.embedding_model = embedding_model
        self.restore_retry_budget = restore_retry_budget
        self.id_factory = id_factory or (lambda _kind: f"art_{uuid4().hex}")
        self.cursor = SignedCursorCodec(secret=cursor_secret)
        self.publisher = AtomicMemoryProjectionPublisher(
            index,
            embedding_model=embedding_model,
            load_tags=load_atomic_memory_tags,
            load_security=load_atomic_memory_security,
        )
        self.service = AtomicMemoryService(
            artifacts=artifacts,
            states=AtomicMemoryStateRepository(),
            security=self.security,
            projections=self.publisher,
            merge_tags=self.merge_tags,
            preview_signer=preview_signer,
        )

    def for_scope(self, scope_id: str) -> ScopedAtomicMemory:
        return ScopedAtomicMemory(self, scope_id)

    async def refresh_access(self, connection, resource) -> None:
        security = await load_atomic_memory_security(connection, resource.scope_id, resource.artifact_id)
        await self.index.refresh_access(
            connection,
            resource.scope_id,
            resource.artifact_id,
            security.owner_type,
            security.owner_id,
            security.read_grants,
        )

    async def merge_tags(self, connection, scope_id: str, result_id: str, input_ids: tuple[str, ...]) -> None:
        from datetime import UTC, datetime
        from hashlib import sha256

        rows = (
            await connection.execute(
                select(ARTIFACT_TAGS_TABLE.c.tag_key, ARTIFACT_TAGS_TABLE.c.tag)
                .where(
                    ARTIFACT_TAGS_TABLE.c.scope_id == scope_id,
                    ARTIFACT_TAGS_TABLE.c.family == "atomic-memory",
                    ARTIFACT_TAGS_TABLE.c.artifact_id.in_(input_ids),
                    ARTIFACT_TAGS_TABLE.c.target_type == "artifact",
                )
                .order_by(ARTIFACT_TAGS_TABLE.c.tag_key, ARTIFACT_TAGS_TABLE.c.artifact_id)
                .with_for_update()
            )
        ).all()
        labels: dict[str, str] = {}
        for row in rows:
            labels.setdefault(str(row.tag_key), str(row.tag))
        labels = normalize_tags(tuple(labels.values()))
        for key, label in labels.items():
            await connection.execute(
                insert(ARTIFACT_TAGS_TABLE).values(
                    scope_id=scope_id,
                    family="atomic-memory",
                    artifact_id=result_id,
                    target_type="artifact",
                    target_id=result_id,
                    tag_key=key,
                    tag_key_hash=sha256(key.encode()).digest(),
                    tag=label,
                    assigned_at=datetime.now(UTC),
                )
            )


class ScopedAtomicMemory:
    def __init__(self, application: AtomicMemoryApplication, scope_id: str) -> None:
        self.application = application
        self.scope_id = scope_id

    def _context(self, context):
        return self.application.default_context if context is None else context

    async def get(self, artifact_id: str, *, revision: int | None = None, context=None) -> AtomicMemoryRecord:
        async with self.application.database.transaction() as connection:
            return await self.application.service.get(
                connection, self.scope_id, artifact_id, self._context(context), revision=revision
            )

    async def list(  # noqa: C901
        self,
        *,
        states: tuple[str, ...] = ("active",),
        kind: str | None = None,
        tag_filter: TagFilter | None = None,
        limit: int = 50,
        cursor: str | None = None,
        context=None,
    ) -> AtomicMemoryPage:
        from powercontext.server.authz import AccessDeniedError

        selected_context = self._context(context)
        _validate_limit(limit)
        if not states or len(set(states)) != len(states):
            raise InvalidBaseAccessRequestError("states", "must contain distinct lifecycle values")
        states = tuple(AtomicMemoryStateValue(state).value for state in states)
        if kind is not None:
            AtomicMemoryContent(kind=kind, text="validation")
        await self.application.security.filters(self.scope_id, selected_context, tags=tag_filter)
        bound = {
            "endpoint": "atomic_memory_list",
            "version": 1,
            "scope_id": self.scope_id,
            "subject": self.application.security.subject(selected_context),
            "states": sorted(states),
            "kind": kind,
            "tags": None if tag_filter is None else list(tag_filter.keys),
            "tag_match": None if tag_filter is None else tag_filter.match,
        }
        after = self.application.cursor.after_text(cursor, bound)
        items: list[AtomicMemoryRecord] = []
        last = after
        has_more = False
        table = ATOMIC_MEMORY_STATES_TABLE
        head = ARTIFACT_HEADS_TABLE
        async with self.application.database.transaction() as connection:
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN")
            while len(items) <= limit:
                statement = (
                    select(table.c.artifact_id)
                    .join(
                        head,
                        (head.c.scope_id == table.c.scope_id)
                        & (head.c.artifact_id == table.c.artifact_id)
                        & (head.c.family == "atomic-memory"),
                    )
                    .where(table.c.scope_id == self.scope_id, table.c.artifact_id > last, table.c.state.in_(states))
                    .order_by(table.c.artifact_id)
                    .limit(100)
                )
                if tag_filter is not None:
                    statement = statement.where(
                        tag_predicate(
                            self.scope_id,
                            "atomic-memory",
                            table.c.artifact_id,
                            "artifact",
                            table.c.artifact_id,
                            tag_filter,
                        )
                    )
                rows = (await connection.execute(statement)).scalars().all()
                if not rows:
                    break
                for artifact_id in rows:
                    try:
                        # Scope-wide reads remain authorized by the same formal policy.
                        record = await self.application.service.get(
                            connection, self.scope_id, str(artifact_id), selected_context
                        )
                    except AccessDeniedError:
                        last = str(artifact_id)
                        continue
                    if kind is not None and record.artifact.content.kind != kind:
                        last = str(artifact_id)
                        continue
                    if len(items) == limit:
                        has_more = True
                        break
                    items.append(record)
                    last = str(artifact_id)
                if has_more or len(rows) < 100:
                    break
        return AtomicMemoryPage(tuple(items), self.application.cursor.encode(bound, last) if has_more else None)

    async def search(
        self,
        query: str,
        *,
        mode: str = "text",
        limit: int = 20,
        kind: str | None = None,
        tag_filter: TagFilter | None = None,
        context=None,
        admission: AdmissionFloor | None = None,
        query_embedding: MemoryQueryEmbedding | None = None,
    ) -> AtomicMemorySearchPage:
        application = self.application
        _validate_limit(limit)
        normalize_query(query)
        if mode not in {"auto", "text", "vector", "hybrid"}:
            raise InvalidBaseAccessRequestError("mode", "must be auto, text, vector, or hybrid")
        if kind is not None:
            AtomicMemoryContent(kind=kind, text="validation")
        filters = await application.security.filters(self.scope_id, self._context(context), tags=tag_filter)
        filters = replace(filters, kind=kind)
        profile = application.index.capabilities.embedding_profile
        if mode == "auto":
            mode = "hybrid" if profile is not None else "text"
        vector = None
        embedding_calls = 0
        if mode in {"vector", "hybrid"}:
            model = application.embedding_model
            if profile is None or model is None or model.profile != profile:
                raise AtomicMemoryIndexError("embedding-profile", "Requested vector retrieval is unavailable")
            if query_embedding is not None and query_embedding.embedding_profile == profile:
                vector = query_embedding.query_vector
            else:
                result = await model.embed((query,))
                embedding_calls = 1
                if len(result.vectors) != 1:
                    raise AtomicMemoryIndexError("embedding-result", "Expected one query vector")
                vector = canonical_embedding(
                    result.vectors[0], dimension=profile.dimension, normalization=profile.normalization
                )
                query_embedding = MemoryQueryEmbedding(vector, profile)
        request = AtomicMemorySearchRequest(
            query,
            filters,
            mode=cast(AtomicMemorySearchMode, "fts" if mode == "text" else mode),
            limit=limit if application.reranker is None else max(limit, application.rerank_candidate_limit),
            query_vector=vector,
            embedding_profile=profile,
            admission=admission,
        )
        async with application.database.transaction() as connection:
            channels = await application.index.search(connection, self.scope_id, request)
        fts = {_artifact_key(item.artifact_ref) for item in channels.fts}
        vectors = {_artifact_key(item.artifact_ref) for item in channels.vector}
        hits = combine_atomic_memory_channels(channels)[: request.limit]
        candidates = tuple(
            AtomicMemorySearchHit(
                hit,
                tuple(
                    channel
                    for channel, refs in (("text", fts), ("vector", vectors))
                    if _artifact_key(hit.artifact_ref) in refs
                ),
            )
            for hit in hits
        )
        return await self._rerank(
            query,
            mode,
            candidates,
            limit,
            query_embedding if vector is not None else None,
            embedding_calls,
            self._context(context),
        )

    async def _rerank(self, query, mode, candidates, limit, query_embedding, embedding_calls, context):
        application = self.application
        reranker = application.reranker
        if reranker is None or not candidates:
            return AtomicMemorySearchPage(mode, candidates[:limit], query_embedding, embedding_calls)
        # Reauthorize the exact candidate bodies immediately before an external rank model.
        async with application.database.transaction() as connection:
            for candidate in candidates:
                current = await application.service.get(
                    connection, self.scope_id, candidate.hit.artifact_ref.artifact_id, context
                )
                if (
                    current.ref != candidate.hit.artifact_ref
                    or current.state.state_version != candidate.hit.state_version
                    or current.state.state is not AtomicMemoryStateValue.ACTIVE
                ):
                    raise AtomicMemoryConflictError("Rerank candidate changed")  # noqa: TRY003
        prompt = (
            None if application.prompt_context_factory is None else application.prompt_context_factory(self.scope_id)
        )
        binding = nullcontext() if prompt is None else prompt.service.bind(self.scope_id, "memory.rerank")
        started = perf_counter()
        async with binding:
            decision = await reranker.rerank(query, candidates, min(limit, len(candidates)))
        ranks = decision.selected_ranks
        if (
            len(ranks) > limit
            or len(set(ranks)) != len(ranks)
            or any(rank < 1 or rank > len(candidates) for rank in ranks)
        ):
            raise InvalidInferenceOutputError("atomic-memory-rerank", "Reranker returned invalid candidate ranks")
        trace = AtomicMemoryRerankTrace(
            reranker.policy_id,
            candidates,
            ranks,
            decision.discarded_rank_count,
            decision.used_fallback,
            (perf_counter() - started) * 1000,
            decision.usage,
        )
        return AtomicMemorySearchPage(
            mode, tuple(candidates[rank - 1] for rank in ranks), query_embedding, embedding_calls, 1, rerank=trace
        )

    async def merge(
        self, inputs: tuple[AtomicMemoryRead, ...], content: AtomicMemoryContent, *, lineage=None, context=None
    ):
        application = self.application
        context = self._context(context)
        async with application.database.transaction() as connection:
            plan = await application.service.inspect_merge(
                connection,
                self.scope_id,
                application.id_factory("atomic-memory"),
                inputs,
                content,
                context,
                lineage=lineage,
            )
        prepared = await application.service.prepare_merge(plan)
        async with application.database.transaction() as connection:
            return await application.service.commit(connection, prepared, context)

    async def forget(self, artifact_id: str, *, expected_revision: int, expected_state_version: int, context=None):
        application = self.application
        context = self._context(context)
        async with application.database.transaction() as connection:
            plan = await application.service.inspect_forget(
                connection,
                self.scope_id,
                artifact_id,
                context,
                expected_revision=expected_revision,
                expected_state_version=expected_state_version,
            )
        prepared = await application.service.prepare_forget(plan)
        async with application.database.transaction() as connection:
            return await application.service.commit(connection, prepared, context)

    async def preview_restoration(self, artifact_id: str, *, operation="restore", revision=None, context=None):
        application = self.application
        context = self._context(context)
        async with application.database.transaction() as connection:
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("BEGIN")
            plan = await application.service.inspect_restore(
                connection, self.scope_id, artifact_id, context, operation=operation, revision=revision
            )
        return application.service.restoration_preview(plan)

    async def restore(self, artifact_id: str, *, operation="restore", revision=None, preview_token=None, context=None):
        application = self.application
        context = self._context(context)
        for attempt in range(application.restore_retry_budget + 1):
            try:
                async with application.database.transaction() as connection:
                    plan = await application.service.inspect_restore(
                        connection,
                        self.scope_id,
                        artifact_id,
                        context,
                        operation=operation,
                        revision=revision,
                        preview_token=preview_token,
                    )
                prepared = await application.service.prepare_restore(plan)
                async with application.database.transaction() as connection:
                    return await application.service.commit(connection, prepared, context)
            except AtomicMemoryConflictError as error:
                if preview_token is not None:
                    if isinstance(error, AtomicMemoryPreviewStaleError):
                        raise
                    raise AtomicMemoryPreviewStaleError("restoration preview is stale") from error  # noqa: TRY003
                if attempt == application.restore_retry_budget:
                    raise
        raise AssertionError("restoration retry loop ended without a result")  # noqa: TRY003


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise InvalidBaseAccessRequestError("limit", "must be between 1 and 100")


def _artifact_key(ref) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision
