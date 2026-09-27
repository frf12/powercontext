# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Native OceanBase/SeekDB VECTOR and HNSW storage for active E/S/T heads."""

from __future__ import annotations

from collections.abc import Sequence

from pyobvector import VECTOR, VectorIndex
from sqlalchemy import Column, Table, bindparam, delete, insert, select, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.artifact_vector_index import (
    ArtifactKey,
    ArtifactVectorHit,
    ArtifactVectorProjection,
    active_vector_predicate,
    artifact_key,
    matching_projections,
    profile_fingerprint,
    vector_entries_table,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE


class OceanBaseArtifactVectorIndex:
    def __init__(self, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise CapabilityNotSupportedError("vector", "Artifact vectors require normalized L2 embeddings")
        self.profile = profile
        self.fingerprint = profile_fingerprint(profile)
        self.table = vector_entries_table(Column("embedding", VECTOR(profile.dimension), nullable=False))
        self.tables: tuple[Table, ...] = (self.table,)
        self._vector_index = VectorIndex(
            "ix_pc_artifact_vector_embedding", self.table.c.embedding, params="distance=l2,type=hnsw"
        )

    async def initialize(self, connection: AsyncConnection) -> None:
        column_type = await connection.scalar(
            text(
                "SELECT COLUMN_TYPE FROM information_schema.columns WHERE table_schema = DATABASE() "
                "AND table_name = 'pc_artifact_vector_entries' AND column_name = 'embedding'"
            )
        )
        if str(column_type).upper() != f"VECTOR({self.profile.dimension})":
            raise RuntimeError(  # noqa: TRY003
                "Artifact vector dimension changed; rebuild the vector index offline before restarting"
            )
        try:
            await connection.run_sync(lambda sync: self._vector_index.create(sync, checkfirst=True))
        except DBAPIError:
            # Two runtime hosts may initialize the same derived index concurrently.
            exists = await connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
                    "AND table_name = 'pc_artifact_vector_entries' AND index_name = 'ix_pc_artifact_vector_embedding'"
                )
            )
            if not exists:
                raise

    async def matching(
        self, connection: AsyncConnection, scope_id: str, projections: Sequence[ArtifactVectorProjection]
    ) -> frozenset[ArtifactKey]:
        return await matching_projections(connection, self.table, scope_id, self.fingerprint, projections)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projection: ArtifactVectorProjection,
        vector: tuple[float, ...],
    ) -> None:
        ref = projection.ref
        columns = self.table.c
        await connection.execute(
            delete(self.table).where(
                columns.scope_id == scope_id,
                columns.family == ref.family,
                columns.artifact_id == ref.artifact_id,
            )
        )
        await connection.execute(
            insert(self.table).values(
                scope_id=scope_id,
                family=ref.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
                profile_fingerprint=self.fingerprint,
                embedding_hash=projection.embedding_hash,
                embedding=validate_embedding(vector, dimension=self.profile.dimension),
            )
        )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query_vector: tuple[float, ...],
        *,
        families: tuple[str, ...],
        allowed: tuple[ArtifactRef, ...] | None = None,
        limit: int = 128,
    ) -> tuple[ArtifactVectorHit, ...]:
        if not families or allowed == ():
            return ()
        columns = self.table.c
        eligible = (
            select(columns.vector_id)
            .join(ARTIFACT_HEADS_TABLE, active_vector_predicate(self.table))
            .where(
                columns.scope_id == scope_id,
                columns.family.in_(families),
                columns.profile_fingerprint == self.fingerprint,
            )
        )
        if allowed is not None:
            eligible = eligible.where(
                tuple_(columns.family, columns.artifact_id, columns.revision).in_(
                    tuple(artifact_key(ref) for ref in allowed)
                )
            )
        ids = tuple((await connection.execute(eligible)).scalars())
        if not ids:
            return ()
        # Evaluate a small admitted host set exactly, as existing tagged Memory
        # search does. Unrestricted family search uses the native ANN index.
        sql = (
            "SELECT family, artifact_id, revision, l2_distance(embedding, :query_vector) AS distance "
            "FROM pc_artifact_vector_entries WHERE scope_id = :scope AND vector_id IN :ids "
            "ORDER BY l2_distance(embedding, :query_vector) APPROXIMATE LIMIT :limit"
        )
        if allowed is not None:
            sql = sql.replace(" APPROXIMATE", "")
        statement = text(sql).bindparams(
            bindparam("ids", expanding=True), bindparam("query_vector", type_=VECTOR(self.profile.dimension))
        )
        rows = await connection.execute(
            statement,
            {
                "scope": scope_id,
                "ids": ids,
                "limit": limit,
                "query_vector": validate_embedding(query_vector, dimension=self.profile.dimension),
            },
        )
        return tuple(
            ArtifactVectorHit(
                ArtifactRef(family=row.family, artifact_id=row.artifact_id, revision=row.revision), float(row.distance)
            )
            for row in rows
        )
