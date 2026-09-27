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

"""Shared reciprocal-rank fusion, independent of Artifact Family identity."""

from collections.abc import Hashable, Sequence
from math import isfinite
from typing import TypeVar

Identity = TypeVar("Identity", bound=Hashable)
_RRF_CONSTANT = 60
MIN_SEMANTIC_SIMILARITY = 0.3


def reciprocal_rank_scores(
    rankings: Sequence[Sequence[Identity]],
    *,
    weights: Sequence[float] | None = None,
    normalize: bool = False,
) -> dict[Identity, float]:
    """Fuse ranks once per channel; optionally scale by the maximum possible score.

    Enabled but empty channels remain in the denominator. Missing hits must not
    inflate a candidate's score. The unweighted default retains raw RRF scores.
    """
    if not rankings:
        return {}
    channel_weights = tuple(weights) if weights is not None else (1.0,) * len(rankings)
    if len(channel_weights) != len(rankings) or any(w < 0 or not isfinite(w) for w in channel_weights):
        raise ValueError("RRF weights must be finite, nonnegative and match the channels")  # noqa: TRY003
    largest = max(channel_weights)
    if largest == 0:
        raise ValueError("At least one RRF weight must be positive")  # noqa: TRY003
    if normalize:
        # Rescale before summing so large, finite relative weights cannot overflow.
        channel_weights = tuple(w / largest for w in channel_weights)
    maximum = sum(channel_weights) / (_RRF_CONSTANT + 1) if normalize else 1.0
    scores: dict[Identity, float] = {}
    for ranking, weight in zip(rankings, channel_weights, strict=True):
        if weight == 0:
            continue
        seen: set[Identity] = set()
        for rank, identity in enumerate(ranking, start=1):
            if identity in seen:
                continue
            seen.add(identity)
            scores[identity] = scores.get(identity, 0.0) + weight / (_RRF_CONSTANT + rank)
    return {key: min(1.0, score / maximum) for key, score in scores.items()} if normalize else scores
