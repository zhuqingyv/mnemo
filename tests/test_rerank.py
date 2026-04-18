"""Unit tests for authority + contradiction rerank — pure, no DB."""

from __future__ import annotations

import math

import pytest

from mnemo.ranking.rerank import (
    CONTRADICTION_PENALTY,
    SCOPE_MISMATCH_PENALTY,
    apply_rerank,
)


def _fused(
    kid: int,
    rrf: float,
    *,
    source: str = "both",
    fts_rank: int | None = 1,
    vec_rank: int | None = 1,
    **extra,
) -> dict:
    base = {
        "id": kid,
        "rrf_score": rrf,
        "fts_rank": fts_rank,
        "vec_rank": vec_rank,
        "source": source,
    }
    base.update(extra)
    return base


def test_zero_authority_no_contradiction_is_identity():
    fused = [_fused(1, 0.02), _fused(2, 0.015)]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
    )
    assert [e["id"] for e in out] == [1, 2]
    for e in out:
        assert e["authority"] == 0.0
        assert e["authority_mult"] == 1.0
        assert e["contradiction_penalty"] == 1.0
        assert math.isclose(e["final_score"], e["rrf_score"])


def test_authority_can_flip_adjacent_ranks():
    # id=1 rrf=0.0154, authority=0 -> final=0.0154
    # id=2 rrf=0.0150, authority=3 -> final=0.0150*1.3=0.0195
    fused = [_fused(1, 0.0154), _fused(2, 0.0150)]
    authority = {1: 0.0, 2: 3.0}
    out = apply_rerank(
        fused,
        authority_lookup=authority.get,
        contradiction_lookup=lambda _kid: False,
    )
    assert [e["id"] for e in out] == [2, 1]
    assert math.isclose(out[0]["final_score"], 0.0150 * 1.3)


def test_contradiction_applies_0_7_penalty():
    fused = [_fused(1, 0.02)]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: True,
    )
    assert out[0]["contradiction_penalty"] == CONTRADICTION_PENALTY
    assert math.isclose(out[0]["final_score"], 0.02 * CONTRADICTION_PENALTY)


def test_authority_and_contradiction_stack_multiplicatively():
    fused = [_fused(1, 0.02)]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 2.0,
        contradiction_lookup=lambda _kid: True,
        alpha=0.1,
    )
    # (1 + 0.1*2) * 0.7 = 1.2 * 0.7 = 0.84
    assert math.isclose(out[0]["authority_mult"], 1.2)
    assert out[0]["contradiction_penalty"] == 0.7
    assert math.isclose(out[0]["final_score"], 0.02 * 1.2 * 0.7)


def test_rrf_fields_preserved():
    fused = [_fused(1, 0.02, fts_rank=3, vec_rank=None, source="fts_only")]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
    )
    assert out[0]["fts_rank"] == 3
    assert out[0]["vec_rank"] is None
    assert out[0]["source"] == "fts_only"
    assert out[0]["rrf_score"] == 0.02  # untouched


def test_input_list_not_mutated():
    entry = _fused(1, 0.02)
    apply_rerank(
        [entry],
        authority_lookup=lambda _kid: 1.0,
        contradiction_lookup=lambda _kid: True,
    )
    assert "final_score" not in entry
    assert "authority" not in entry


def test_vec_only_gate_drops_low_scoring_candidates():
    # Pure-vector path: all candidates vec_only, top final below gate -> empty
    fused = [
        _fused(1, 0.0164, source="vec_only", fts_rank=None, vec_rank=1),
        _fused(2, 0.0156, source="vec_only", fts_rank=None, vec_rank=2),
    ]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        vec_only_min_final=0.018,
    )
    assert out == []


def test_vec_only_gate_keeps_high_authority_candidates():
    # Same top rrf, but candidate has authority -> final above gate -> kept
    fused = [
        _fused(1, 0.0164, source="vec_only", fts_rank=None, vec_rank=1),
    ]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 2.0,  # 1+0.2 = 1.2x -> 0.01968
        contradiction_lookup=lambda _kid: False,
        vec_only_min_final=0.018,
    )
    assert len(out) == 1
    assert out[0]["id"] == 1


def test_gate_does_not_fire_when_any_fts_hit_present():
    # Hybrid path (at least one fts/both source) — gate must not apply
    fused = [
        _fused(1, 0.0164, source="fts_only", fts_rank=1, vec_rank=None),
        _fused(2, 0.0150, source="vec_only", fts_rank=None, vec_rank=2),
    ]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        vec_only_min_final=0.5,  # absurdly high — would nuke everything if applied
    )
    assert len(out) == 2  # both survive because path isn't vec-only


def test_gate_none_is_disabled():
    fused = [_fused(1, 0.001, source="vec_only", fts_rank=None, vec_rank=1)]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        vec_only_min_final=None,
    )
    assert len(out) == 1


def test_empty_input_stays_empty():
    assert (
        apply_rerank(
            [],
            authority_lookup=lambda _kid: 0.0,
            contradiction_lookup=lambda _kid: False,
        )
        == []
    )


def test_rejects_negative_alpha():
    with pytest.raises(ValueError):
        apply_rerank(
            [_fused(1, 0.02)],
            authority_lookup=lambda _kid: 0.0,
            contradiction_lookup=lambda _kid: False,
            alpha=-0.1,
        )


def test_stable_sort_desc_by_final():
    fused = [
        _fused(1, 0.0100),
        _fused(2, 0.0300),
        _fused(3, 0.0200),
    ]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
    )
    assert [e["id"] for e in out] == [2, 3, 1]


def test_scope_penalty_fires_on_unscoped_query_hitting_project():
    fused = [_fused(1, 0.02, scope="project")]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        query_scope=None,
    )
    assert out[0]["scope_penalty"] == SCOPE_MISMATCH_PENALTY
    assert math.isclose(out[0]["final_score"], 0.02 * SCOPE_MISMATCH_PENALTY)


def test_scope_penalty_skipped_when_query_is_scoped():
    # User asked with scope="project" — they *want* project results. No penalty.
    fused = [_fused(1, 0.02, scope="project")]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        query_scope="project",
    )
    assert out[0]["scope_penalty"] == 1.0
    assert math.isclose(out[0]["final_score"], 0.02)


def test_scope_penalty_skipped_for_global_hit():
    fused = [_fused(1, 0.02, scope="global")]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        query_scope=None,
    )
    assert out[0]["scope_penalty"] == 1.0


def test_scope_penalty_absent_scope_field_is_safe():
    # Older fused dicts may not carry scope at all — must not crash, must not penalize.
    fused = [{"id": 1, "rrf_score": 0.02, "fts_rank": 1, "vec_rank": 1, "source": "both"}]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 0.0,
        contradiction_lookup=lambda _kid: False,
        query_scope=None,
    )
    assert out[0]["scope_penalty"] == 1.0
    assert math.isclose(out[0]["final_score"], 0.02)


def test_scope_penalty_stacks_multiplicatively_with_authority():
    fused = [_fused(1, 0.02, scope="project")]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 2.0,
        contradiction_lookup=lambda _kid: False,
        alpha=0.1,
        query_scope=None,
    )
    # authority_mult (1 + 0.1*2 = 1.2) * scope_penalty (default) compose.
    expected = 0.02 * 1.2 * SCOPE_MISMATCH_PENALTY
    assert math.isclose(out[0]["final_score"], expected)


def test_scope_penalty_rejects_non_positive():
    with pytest.raises(ValueError):
        apply_rerank(
            [_fused(1, 0.02)],
            authority_lookup=lambda _kid: 0.0,
            contradiction_lookup=lambda _kid: False,
            scope_mismatch_penalty=0,
        )


def test_m4_grid_search_combo_catches_rel_n06():
    """M4 task #3 grid search tightened scope_mismatch_penalty 0.8 → 0.6 so
    REL-N-06's vec_only top-1 drops below vec_only_min_final=0.017.

    REL-N-06 "kubernetes 集群" pulls project-scoped candidates with rrf≈0.018
    and mild authority — under 0.8 scope_penalty it slipped through (0.018 *
    1.0 * 0.8 = 0.0144 * (1+0.05*1.0) ≈ 0.0151, marginally above 0.0170 when
    authority is higher). At 0.6 the multiplier knocks any plausible
    project-scope candidate below the gate regardless of authority level in
    the fixture range.
    """
    fused = [_fused(1, 0.018, source="vec_only", fts_rank=None, vec_rank=1,
                    scope="project")]
    out = apply_rerank(
        fused,
        authority_lookup=lambda _kid: 2.0,
        contradiction_lookup=lambda _kid: False,
        alpha=0.05,
        vec_only_min_final=0.017,
        query_scope=None,
        scope_mismatch_penalty=0.6,
    )
    # (1 + 0.05*2.0) * 0.6 = 0.66 → 0.018 * 0.66 = 0.01188 < 0.017 → dropped
    assert out == []


def test_scope_penalty_matches_calibrated_separation():
    # Regression against the M3b calibration data:
    # - REL-N-10 OOD hit: rrf=0.0159, auth=1.95, scope=project -> final ~ 0.01517
    # - INT-20 positive:   rrf=0.0137, auth=2.71, scope=global  -> final ~ 0.01741
    # The gate at 0.017 must drop the OOD hit but keep the INT positive.
    fused_ood = [_fused(10, 0.0159, source="vec_only", fts_rank=None, vec_rank=1, scope="project")]
    fused_int = [_fused(20, 0.0137, source="vec_only", fts_rank=None, vec_rank=1, scope="global")]

    out_ood = apply_rerank(
        fused_ood,
        authority_lookup=lambda _kid: 1.95,
        contradiction_lookup=lambda _kid: False,
        alpha=0.1,
        vec_only_min_final=0.017,
        query_scope=None,
    )
    assert out_ood == []

    out_int = apply_rerank(
        fused_int,
        authority_lookup=lambda _kid: 2.71,
        contradiction_lookup=lambda _kid: False,
        alpha=0.1,
        vec_only_min_final=0.017,
        query_scope=None,
    )
    assert len(out_int) == 1
    assert out_int[0]["final_score"] >= 0.017
