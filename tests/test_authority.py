"""Unit tests for authority score formula — pure, no DB."""

from __future__ import annotations

import math

import pytest

from mnemo.ranking.authority import (
    AUTHORITY_INCOMING_TYPES,
    authority_multiplier,
    authority_score,
)


def test_isolated_node_scores_zero():
    assert authority_score({}) == 0.0
    assert authority_score({"supersedes": 0, "refines": 0, "derived_from": 0}) == 0.0


def test_single_refines_matches_techplan():
    # log(1 + 1.5*1) = log(2.5)
    assert math.isclose(authority_score({"refines": 1}), math.log(2.5))


def test_supersedes_plus_refines_matches_techplan():
    # log(1 + 2 + 1.5) = log(4.5) ~ 1.504
    assert math.isclose(
        authority_score({"supersedes": 1, "refines": 1}),
        math.log(4.5),
    )


def test_hotspot_matches_techplan():
    # 5 sup + 3 ref + 2 derived -> log(1 + 10 + 4.5 + 2) = log(17.5) ~ 2.86
    score = authority_score(
        {"supersedes": 5, "refines": 3, "derived_from": 2}
    )
    assert math.isclose(score, math.log(17.5))


def test_unknown_types_ignored():
    # related / wikilink / depends_on etc. must not influence authority
    score = authority_score(
        {"related": 99, "wikilink": 50, "depends_on": 7, "contradicts": 3}
    )
    assert score == 0.0


def test_contradicts_does_not_boost_authority():
    # contradicts is a penalty, not a boost — it cannot feed authority
    assert authority_score({"contradicts": 10}) == 0.0


def test_negative_count_rejected():
    with pytest.raises(ValueError):
        authority_score({"supersedes": -1})


def test_multiplier_defaults_to_alpha_0_1():
    # alpha=0.1, authority=3 -> 1.3
    assert math.isclose(authority_multiplier(3.0), 1.3)


def test_multiplier_alpha_scales_linearly():
    assert math.isclose(authority_multiplier(2.0, alpha=0.05), 1.1)
    assert math.isclose(authority_multiplier(2.0, alpha=0.15), 1.3)


def test_multiplier_zero_authority_is_identity():
    assert authority_multiplier(0.0) == 1.0
    assert authority_multiplier(0.0, alpha=0.5) == 1.0


def test_multiplier_rejects_negative_inputs():
    with pytest.raises(ValueError):
        authority_multiplier(-0.1)
    with pytest.raises(ValueError):
        authority_multiplier(1.0, alpha=-0.1)


def test_incoming_types_are_the_ones_that_feed_authority():
    # Guard: if someone renames a constant, this should trip immediately.
    assert set(AUTHORITY_INCOMING_TYPES) == {
        "supersedes",
        "refines",
        "derived_from",
    }
