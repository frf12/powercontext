# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Persistent sqlite-vec projection for active Experience, Skill and Tool heads."""

from __future__ import annotations

import re
import struct
from collections.abc import Sequence

from sqlalchemy import Table, bindparam, delete, insert, select, text, tuple_
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


class SQLiteArtifactVectorIndex:
    def __init__(self, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise CapabilityNotSupportedError("vector", "Artifact vectors require normalized L2 embeddings")
        self.profile = profile
        self.fingerprint = profile_fingerprint(profile)
        self.table = vector_entries_table()
        self.tables: tuple[Table, ...] = (self.table,)

    async def initialize(self, connection: AsyncConnection) -> None:
        await connection.exec_driver_sql("SELECT vec_version()")
        await connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS pc_artifact_vec "
            f"USING vec0(scope_id TEXT partition key, embedding float[{self.profile.dimension}])"
        )
        definition = str(await connection.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'pc_artifact_vec'")))
        dimension = re.search(r"embedding\s+float\[(\d+)\]", definition, re.IGNORECASE)
        if dimension is None or int(dimension[1]) != self.profile.dimension:
            raise RuntimeError(  # noqa: TRY003
                "Artifact vector dimension changed; rebuild the vector index offline before restarting"
            )
        # Interrupted index writes must not leave apparently complete metadata.
        await connection.exec_driver_sql(
            "DELETE FROM pc_artifact_vector_entries WHERE vector_id NOT IN (SELECT rowid FROM pc_artifact_vec)"
        )
        await connection.exec_driver_sql(
            "DELETE FROM pc_artifact_vec WHERE rowid NOT IN (SELECT vector_id FROM pc_artifact_vector_entries)"
        )
        probe = _pack((0.0,) * self.profile.dimension)
        await connection.exec_driver_sql("DELETE FROM pc_artifact_vec WHERE rowid = -1")
        await connection.exec_driver_sql(
            "INSERT INTO pc_artifact_vec(rowid, scope_id, embedding) VALUES(-1, ?, ?)",
            ("__artifact_vector_probe__", probe),
        )
        await connection.exec_driver_sql(
            "SELECT rowid FROM pc_artifact_vec WHERE scope_id = ? AND embedding MATCH ? AND k = 1",
            ("__artifact_vector_probe__", probe),
        )
        await connection.exec_driver_sql("DELETE FROM pc_artifact_vec WHERE rowid = -1")

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
        identity = (
            columns.scope_id == scope_id,
            columns.family == ref.family,
            columns.artifact_id == ref.artifact_id,
        )
        previous = await connection.scalar(select(columns.vector_id).where(*identity))
        if previous is not None:
            await connection.execute(text("DELETE FROM pc_artifact_vec WHERE rowid = :id"), {"id": previous})
            await connection.execute(delete(self.table).where(*identity))
        result = await connection.execute(
            insert(self.table).values(
                scope_id=scope_id,
                family=ref.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
                profile_fingerprint=self.fingerprint,
                embedding_hash=projection.embedding_hash,
            )
        )
        await connection.execute(
            text("INSERT INTO pc_artifact_vec(rowid, scope_id, embedding) VALUES(:id, :scope, :vector)"),
            {
                "id": result.lastrowid,
                "scope": scope_id,
                "vector": _pack(validate_embedding(vector, dimension=self.profile.dimension)),
            },
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
        # sqlite-vec admits rowids before KNN, preserving host and family filters.
        ids = tuple((await connection.execute(eligible)).scalars())
        if not ids:
            return ()
        vector = _pack(validate_embedding(query_vector, dimension=self.profile.dimension))
        if len(ids) == 1:
            # SQLite rewrites a singleton IN into equality. sqlite-vec then
            # applies that rowid filter after KNN, potentially dropping the
            # only admitted candidate. Score this one stored vector directly.
            nearest = (
                await connection.execute(
                    text(
                        "SELECT rowid, vec_distance_l2(embedding, :query_vector) AS distance "
                        "FROM pc_artifact_vec WHERE scope_id = :scope AND rowid = :id"
                    ),
                    {"scope": scope_id, "query_vector": vector, "id": ids[0]},
                )
            ).all()
        else:
            nearest = (
                await connection.execute(
                    text(
                        "SELECT rowid, distance FROM pc_artifact_vec "
                        "WHERE scope_id = :scope AND embedding MATCH :query_vector AND k = :limit AND rowid IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {
                        "scope": scope_id,
                        "query_vector": vector,
                        "limit": min(limit, 4096),
                        "ids": ids,
                    },
                )
            ).all()
        distances = {int(row[0]): float(row[1]) for row in nearest}
        if not distances:
            return ()
        rows = await connection.execute(
            select(columns.vector_id, columns.family, columns.artifact_id, columns.revision)
            .join(ARTIFACT_HEADS_TABLE, active_vector_predicate(self.table))
            .where(columns.scope_id == scope_id, columns.vector_id.in_(tuple(distances)))
        )
        return tuple(
            sorted(
                (
                    ArtifactVectorHit(
                        ArtifactRef(family=row.family, artifact_id=row.artifact_id, revision=row.revision),
                        distances[row.vector_id],
                    )
                    for row in rows
                ),
                key=lambda hit: (hit.distance, artifact_key(hit.ref)),
            )
        )


def _pack(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)
