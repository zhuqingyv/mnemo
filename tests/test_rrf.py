"""Unit tests for rrf_repository.rrf_fuse — pure function, no fixtures needed."""

from __future__ import annotations

import math

from mnemo.repository.rrf_repository import rrf_fuse


def test_both_channels_hit_scores_sum():
    fts = [{"id": 1, "title": "A"}]
    vec = [{"id": 1, "title": "A"}]
    out = rrf_fuse(fts, vec, k=60)
    assert len(out) == 1
    r = out[0]
    assert r["source"] == "both"
    assert r["fts_rank"] == 1
    assert r["vec_rank"] == 1
    assert math.isclose(r["rrf_score"], 1 / 61 + 1 / 61)


def test_single_channel_contributes_only_its_own_term():
    """Miss must contribute 0, not 1/(k+sentinel)."""
    out = rrf_fuse([{"id": 1}], [], k=60)
    assert out[0]["rrf_score"] == 1 / 61
    assert out[0]["fts_rank"] == 1
    assert out[0]["vec_rank"] is None
    assert out[0]["source"] == "fts_only"

    out = rrf_fuse([], [{"id": 2}], k=60)
    assert out[0]["rrf_score"] == 1 / 61
    assert out[0]["fts_rank"] is None
    assert out[0]["vec_rank"] == 1
    assert out[0]["source"] == "vec_only"


def test_dedup_by_id():
    fts = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
    vec = [{"id": 2, "title": "B"}, {"id": 1, "title": "A"}]
    out = rrf_fuse(fts, vec)
    assert len(out) == 2
    assert {r["id"] for r in out} == {1, 2}
    for r in out:
        assert r["source"] == "both"


def test_sorted_descending():
    fts = [{"id": i} for i in range(1, 6)]
    vec = [{"id": 5}, {"id": 4}]
    out = rrf_fuse(fts, vec)
    scores = [r["rrf_score"] for r in out]
    assert scores == sorted(scores, reverse=True)
    assert out[0]["id"] == 5  # hits both, fts rank 5 + vec rank 1


def test_empty_inputs():
    assert rrf_fuse([], []) == []
    assert len(rrf_fuse([{"id": 1}], [])) == 1
    assert len(rrf_fuse([], [{"id": 1}])) == 1


def test_input_dicts_not_mutated():
    fts_hit = {"id": 1, "title": "A"}
    vec_hit = {"id": 1, "title": "A"}
    rrf_fuse([fts_hit], [vec_hit])
    assert "rrf_score" not in fts_hit
    assert "rrf_score" not in vec_hit
    assert "source" not in fts_hit
    assert "fts_rank" not in fts_hit


def test_custom_k():
    out = rrf_fuse([{"id": 1}], [{"id": 1}], k=10)
    assert math.isclose(out[0]["rrf_score"], 1 / 11 + 1 / 11)


def test_fts_fields_preserved_when_both_hit():
    """When both channels hit, the merged entry should keep the richer FTS dict."""
    fts = [{"id": 1, "title": "A", "summary": "from fts"}]
    vec = [{"id": 1, "title": "A"}]
    out = rrf_fuse(fts, vec)
    assert out[0]["summary"] == "from fts"


def test_ordering_fts_rank1_beats_vec_rank5():
    """Sanity: a strong single-channel hit can outrank a weak single-channel hit."""
    out = rrf_fuse(
        [{"id": 10}],
        [{"id": 20}, {"id": 21}, {"id": 22}, {"id": 23}, {"id": 24}],
    )
    assert out[0]["id"] == 10
