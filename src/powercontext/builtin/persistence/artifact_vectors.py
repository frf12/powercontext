# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable Artifact vector publication, explicit backfill and query-only embedding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.fusion import MIN_SEMANTIC_SIMILARITY
from powercontext.builtin.artifacts.memory.canonical import embedding_content_hash, normalize_embedding
from powercontext.builtin.inference.errors import InvalidInferenceOutputError
from powercontext.builtin.inference.protocols import EmbeddingModel
from powercontext.builtin.persistence.artifact_vector_index import (
    VECTOR_FAMILIES,
    ArtifactVectorIndex,
    ArtifactVectorProjection,
    artifact_key,
    content_search_text,
    project_artifact,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE


@dataclass(frozen=True)
class PreparedArtifactVector:
    family: str
    text: str
    embedding_hash: str
    vector: tuple[float, ...]


class ArtifactVectorService:
    def __init__(
        self,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        index: ArtifactVectorIndex,
        embedding: EmbeddingModel,
        *,
        min_similarity: float = MIN_SEMANTIC_SIMILARITY,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.index = index
        self.embedding = embedding
        self.min_similarity = min_similarity

    async def _pending(self, scope_id: str) -> tuple[ArtifactVectorProjection, ...]:
        heads = ARTIFACT_HEADS_TABLE.c
        pending: list[ArtifactVectorProjection] = []
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(heads.family, heads.artifact_id, heads.revision)
                    .where(
                        heads.scope_id == scope_id,
                        heads.family.in_(VECTOR_FAMILIES),
                        heads.lifecycle_state == "active",
                    )
                    .order_by(heads.family, heads.artifact_id)
                )
            ).all()
            # Bound SQL parameter counts independently of the provider's batch setting.
            for start in range(0, len(rows), 100):
                projections = []
                for row in rows[start : start + 100]:
                    artifact = await self.artifacts.get(
                        connection,
                        scope_id,
                        ArtifactRef(
                            family=row.family,
                            artifact_id=row.artifact_id,
                            revision=row.revision,
                        ),
                    )
                    projections.append(project_artifact(artifact, self.index.profile))
                present = await self.index.matching(connection, scope_id, projections)
                pending.extend(item for item in projections if artifact_key(item.ref) not in present)
                if len(pending) >= 100:
                    break
        return tuple(pending[:100])

    async def prepare(self, family: str, content: BaseModel) -> PreparedArtifactVector | None:
        """Compute the projection before the caller opens its publication transaction."""
        if family not in VECTOR_FAMILIES:
            return None
        text = content_search_text(family, content).encode()[:8000].decode(errors="ignore")
        digest = embedding_content_hash(
            **self.index.profile.model_dump(), entry_content_hash=sha256(text.encode()).hexdigest()
        )
        vector = (await self._embed((text,)))[0]
        return PreparedArtifactVector(family, text, digest, vector)

    async def commit(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact: Artifact[Any],
        prepared: PreparedArtifactVector | None,
    ) -> None:
        """Save a prepared vector in the caller's Artifact publication transaction."""
        if artifact.family not in VECTOR_FAMILIES:
            return
        projection = project_artifact(artifact, self.index.profile)
        if (
            prepared is None
            or prepared.family != artifact.family
            or prepared.embedding_hash != projection.embedding_hash
        ):
            raise ValueError("Artifact content changed after vector preparation")  # noqa: TRY003
        await self.index.replace(connection, scope_id, projection, prepared.vector)

    async def rebuild(self, scope_id: str) -> int:
        """Explicitly backfill historical/mismatched vectors; never invoked by queries."""
        count = 0
        while pending := await self._pending(scope_id):
            vectors = await self._embed(tuple(item.text for item in pending))
            async with self.database.transaction() as connection:
                for projection, vector in zip(pending, vectors, strict=True):
                    # Serialize against revision changes/retirement, then publish the
                    # exact projection in the same short transaction.
                    heads = ARTIFACT_HEADS_TABLE.c
                    ref = projection.ref
                    locked = await connection.execute(
                        update(ARTIFACT_HEADS_TABLE)
                        .where(
                            heads.scope_id == scope_id,
                            heads.family == ref.family,
                            heads.artifact_id == ref.artifact_id,
                            heads.revision == ref.revision,
                            heads.lifecycle_state == "active",
                        )
                        .values(revision=heads.revision)
                    )
                    if locked.rowcount != 1:
                        continue
                    # A second worker/backfill may have installed the same revision.
                    if artifact_key(ref) in await self.index.matching(connection, scope_id, (projection,)):
                        continue
                    await self.index.replace(connection, scope_id, projection, vector)
                    count += 1
        return count

    async def _embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        result = await self.embedding.embed(texts)
        if len(result.vectors) != len(texts):
            raise InvalidInferenceOutputError("embed", "vector count does not match text count")
        try:
            return tuple(
                normalize_embedding(vector, dimension=self.index.profile.dimension) for vector in result.vectors
            )
        except ValueError as error:
            raise InvalidInferenceOutputError("embed", "invalid artifact vector") from error

    async def search(
        self,
        scope_id: str,
        query: str,
        *,
        families: tuple[str, ...],
        allowed: tuple[ArtifactRef, ...] | None = None,
        limit: int = 128,
        unfiltered_families: tuple[str, ...] = (),
        min_similarity_by_family: Mapping[str, float] | None = None,
    ) -> tuple[ArtifactRef, ...]:
        if not query.strip() or not families or allowed == ():
            return ()
        vector = (await self._embed((query,)))[0]
        async with self.database.transaction() as connection:
            hits = await self.index.search(
                connection, scope_id, vector, families=families, allowed=allowed, limit=limit
            )
        thresholds = min_similarity_by_family or {}
        return tuple(
            hit.ref
            for hit in hits
            if hit.ref.family in unfiltered_families
            or 1 - hit.distance**2 / 2 >= thresholds.get(hit.ref.family, self.min_similarity)
        )
