"""Reciprocal Rank Fusion for combining FTS and vector search results.

Pure function — no DB, no Ollama. Takes the dicts already produced by
``knowledge_service.search`` and merges them by knowledge id.
"""

from __future__ import annotations

from typing import Any


def rrf_fuse(
    fts_results: list[dict[str, Any]],
    vec_results: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """Fuse FTS and vector hits with Reciprocal Rank Fusion.

    Score = sum over each channel that hit: ``1 / (k + rank)`` (rank is 1-based).
    Misses contribute 0 — we don't penalize them with a sentinel rank.

    Args:
        fts_results: dicts from FTS search, ordered by relevance. Must have "id".
        vec_results: dicts from vector search, ordered by similarity.
        k: RRF constant (default 60, per Cormack et al. 2009).

    Returns:
        Merged dicts sorted by ``rrf_score`` desc. Each dict gets:
        - ``rrf_score``: float
        - ``fts_rank``: 1-based rank or None
        - ``vec_rank``: 1-based rank or None
        - ``source``: "both" | "fts_only" | "vec_only"
        Original fields from the input dict are preserved; when both channels
        hit the same id, the FTS dict wins (vector fields are usually a subset).
    """
    merged: dict[Any, dict[str, Any]] = {}

    for rank, hit in enumerate(fts_results, start=1):
        kid = hit["id"]
        entry = dict(hit)
        entry["fts_rank"] = rank
        entry["vec_rank"] = None
        entry["rrf_score"] = 1.0 / (k + rank)
        merged[kid] = entry

    for rank, hit in enumerate(vec_results, start=1):
        kid = hit["id"]
        contribution = 1.0 / (k + rank)
        if kid in merged:
            merged[kid]["vec_rank"] = rank
            merged[kid]["rrf_score"] += contribution
        else:
            entry = dict(hit)
            entry["fts_rank"] = None
            entry["vec_rank"] = rank
            entry["rrf_score"] = contribution
            merged[kid] = entry

    for entry in merged.values():
        if entry["fts_rank"] is not None and entry["vec_rank"] is not None:
            entry["source"] = "both"
        elif entry["fts_rank"] is not None:
            entry["source"] = "fts_only"
        else:
            entry["source"] = "vec_only"

    return sorted(merged.values(), key=lambda e: e["rrf_score"], reverse=True)
