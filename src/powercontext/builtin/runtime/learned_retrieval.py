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

"""Fuse persistent database vector and full-text recall for an admitted host pool."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, tuple_

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.fusion import reciprocal_rank_scores
from powercontext.builtin.inference.errors import InferenceError
from powercontext.builtin.persistence.artifact_vector_index import ArtifactKey, artifact_key
from powercontext.builtin.persistence.artifact_vectors import ArtifactVectorService
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.experience_index import ExperienceIndex
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearnedRanking:
    scores: dict[ArtifactKey, float]
    fts: tuple[ArtifactKey, ...] = ()
    vector: tuple[ArtifactKey, ...] = ()
    degraded: bool = False
    active_keys: frozenset[ArtifactKey] = frozenset()


class LearnedArtifactRetrieval:
    def __init__(
        self, database: AsyncDatabase, index: ExperienceIndex, vectors: ArtifactVectorService | None = None
    ) -> None:
        self._database = database
        self._index = index
        self._vectors = vectors

    async def rank(
        self,
        scope_id: str,
        artifacts: Sequence[Artifact[Any]],
        query: str,
        *,
        skill_rerank: bool = False,
        skill_min_similarity: float | None = None,
        fts_weight: float = 1.0,
        vector_weight: float = 1.0,
        min_rrf_score: float = 0.0,
    ) -> LearnedRanking:
        refs = tuple(item.as_ref() for item in artifacts)
        if not refs or not query.strip():
            return LearnedRanking({})
        vector: tuple[ArtifactKey, ...] = ()
        degraded = False
        unfiltered_skills = skill_rerank and skill_min_similarity is None
        if self._vectors is not None:
            try:
                matches = await self._vectors.search(
                    scope_id,
                    query,
                    families=tuple(sorted({ref.family for ref in refs})),
                    allowed=refs,
                    limit=min(len(refs), 4096),
                    unfiltered_families=("skill",) if unfiltered_skills else (),
                    min_similarity_by_family={"skill": skill_min_similarity}
                    if skill_min_similarity is not None
                    else None,
                )
                vector = tuple(artifact_key(ref) for ref in matches)
            except InferenceError as error:
                degraded = True
                _logger.warning("Learned vector retrieval fell back to FTS: %s", type(error).__name__)

        async with self._database.transaction() as connection:
            fts_refs = await self._index.search_artifacts(connection, scope_id, query, refs)
            heads = ARTIFACT_HEADS_TABLE.c
            active = set(
                (
                    await connection.execute(
                        select(heads.family, heads.artifact_id, heads.revision).where(
                            heads.scope_id == scope_id,
                            heads.lifecycle_state == "active",
                            tuple_(heads.family, heads.artifact_id, heads.revision).in_(
                                tuple(artifact_key(ref) for ref in refs)
                            ),
                        )
                    )
                ).tuples()
            )
        fts = tuple(artifact_key(ref) for ref in fts_refs if artifact_key(ref) in active)
        vector = tuple(key for key in vector if key in active)
        if self._vectors is None:
            scores = reciprocal_rank_scores((fts,), weights=(fts_weight,), normalize=True) if fts_weight else {}
        else:
            scores = reciprocal_rank_scores((fts, vector), weights=(fts_weight, vector_weight), normalize=True)
        if self._vectors is not None and not degraded:
            # RRF is relative rank, not relevance. A weak lexical match must not
            # bypass a healthy semantic channel's absolute similarity floor.
            admitted = frozenset(vector)
            scores = {
                key: score
                for key, score in scores.items()
                if key in admitted or (unfiltered_skills and key[0] == "skill")
            }
        scores = {key: score for key, score in scores.items() if score >= min_rrf_score}
        return LearnedRanking(scores, fts, vector, degraded, frozenset(active))
