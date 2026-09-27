"""Observable weighting and threshold score semantics for shared RRF."""

import pytest

from powercontext.builtin.artifacts.fusion import reciprocal_rank_scores


def test_weighted_normalized_rrf_lowers_lexical_only_matches():
    rankings = (("lexical", "shared"), ("semantic", "shared"))
    equal = reciprocal_rank_scores(rankings, weights=(1, 1), normalize=True)
    weighted = reciprocal_rank_scores(rankings, weights=(1, 2), normalize=True)
    assert equal["lexical"] == equal["semantic"] == pytest.approx(0.5)
    assert weighted["lexical"] == pytest.approx(1 / 3)
    assert weighted["semantic"] == pytest.approx(2 / 3)
    assert weighted["shared"] == pytest.approx(61 / 62)
    assert {key for key, score in weighted.items() if score >= 0.45} == {"semantic", "shared"}


def test_rrf_scale_does_not_depend_on_missing_results_or_weight_magnitude():
    missing = (("lexical",), ())
    assert reciprocal_rank_scores(missing, weights=(1, 2), normalize=True)["lexical"] == pytest.approx(1 / 3)
    assert reciprocal_rank_scores(missing, weights=(10, 20), normalize=True) == reciprocal_rank_scores(
        missing, weights=(1, 2), normalize=True
    )
    assert reciprocal_rank_scores((("one",), ("one",)))["one"] == pytest.approx(2 / 61)
