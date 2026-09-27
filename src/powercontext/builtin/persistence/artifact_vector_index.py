# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared identity and projection contract for persistent E/S/T vector indexes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import Column, Integer, MetaData, String, Table, UniqueConstraint, and_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, experience_search_text
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import embedding_content_hash
from powercontext.builtin.artifacts.skill import SkillContent, skill_search_text
from powercontext.builtin.artifacts.tool import ToolContent
from powercontext.builtin.artifacts.tool.search import tool_search_text
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, identity_string
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

VECTOR_FAMILIES = ("experience", "skill", "tool")
ArtifactKey = tuple[str, str, int]


def artifact_key(ref: ArtifactRef) -> ArtifactKey:
    return ref.family, ref.artifact_id, ref.revision


def artifact_search_text(artifact: Artifact[Any]) -> str:
    return content_search_text(artifact.family, artifact.content)


def content_search_text(family: str, content: BaseModel) -> str:
    if family == "experience" and isinstance(content, ExperienceContent):
        return experience_search_text(content)
    if family == "skill" and isinstance(content, SkillContent):
        return skill_search_text(content)
    if family == "tool" and isinstance(content, ToolContent):
        return tool_search_text(content)
    raise ValueError("Unsupported Artifact vector content")  # noqa: TRY003


def profile_fingerprint(profile: EmbeddingProfile) -> str:
    # Version the text projection independently of provider/model identity.
    return sha256(("artifact-vector-v1\0" + profile.model_dump_json()).encode()).hexdigest()


@dataclass(frozen=True)
class ArtifactVectorProjection:
    ref: ArtifactRef
    text: str
    embedding_hash: str


def project_artifact(artifact: Artifact[Any], profile: EmbeddingProfile) -> ArtifactVectorProjection:
    text = artifact_search_text(artifact).encode()[:8000].decode(errors="ignore")
    digest = embedding_content_hash(**profile.model_dump(), entry_content_hash=sha256(text.encode()).hexdigest())
    return ArtifactVectorProjection(artifact.as_ref(), text, digest)


@dataclass(frozen=True)
class ArtifactVectorHit:
    ref: ArtifactRef
    distance: float


class ArtifactVectorIndex(Protocol):
    profile: EmbeddingProfile
    fingerprint: str
    tables: tuple[Table, ...]

    async def initialize(self, connection: AsyncConnection) -> None: ...

    async def matching(
        self, connection: AsyncConnection, scope_id: str, projections: Sequence[ArtifactVectorProjection]
    ) -> frozenset[ArtifactKey]: ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projection: ArtifactVectorProjection,
        vector: tuple[float, ...],
    ) -> None: ...

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query_vector: tuple[float, ...],
        *,
        families: tuple[str, ...],
        allowed: tuple[ArtifactRef, ...] | None = None,
        limit: int = 128,
    ) -> tuple[ArtifactVectorHit, ...]: ...


def vector_entries_table(*extra_columns: Column[Any]) -> Table:
    return Table(
        "pc_artifact_vector_entries",
        MetaData(),
        Column("vector_id", Integer, primary_key=True, autoincrement=True),
        Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
        Column("family", identity_string(64), nullable=False),
        Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
        Column("revision", Integer, nullable=False),
        Column("profile_fingerprint", String(64), nullable=False),
        Column("embedding_hash", String(71), nullable=False),
        *extra_columns,
        UniqueConstraint("scope_id", "family", "artifact_id", name="uq_pc_artifact_vector_head"),
        sqlite_autoincrement=True,
    )


def active_vector_predicate(table: Table):
    heads = ARTIFACT_HEADS_TABLE.c
    return and_(
        heads.scope_id == table.c.scope_id,
        heads.family == table.c.family,
        heads.artifact_id == table.c.artifact_id,
        heads.revision == table.c.revision,
        heads.lifecycle_state == "active",
    )


async def matching_projections(
    connection: AsyncConnection,
    table: Table,
    scope_id: str,
    fingerprint: str,
    projections: Sequence[ArtifactVectorProjection],
) -> frozenset[ArtifactKey]:
    if not projections:
        return frozenset()
    columns = table.c
    rows = await connection.execute(
        select(columns.family, columns.artifact_id, columns.revision).where(
            columns.scope_id == scope_id,
            columns.profile_fingerprint == fingerprint,
            tuple_(columns.family, columns.artifact_id, columns.revision, columns.embedding_hash).in_(
                tuple((*artifact_key(item.ref), item.embedding_hash) for item in projections)
            ),
        )
    )
    return frozenset(tuple(row) for row in rows)
