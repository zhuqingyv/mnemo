"""Authority + contradiction + scope + freshness rerank on top of RRF.

Pure function — DB access is factored out into callable lookups so unit tests
can exercise the ranking math without touching a session.

    final = rrf_score
          * (1 + alpha*authority)
          * contradiction_penalty
          * scope_mismatch_penalty
          * freshness_mult

``freshness_mult`` comes from ``freshness_lookup`` (Phase 3 P3a-M2). When the
callable is ``None`` the multiplier degrades to ``1.0`` — i.e. Phase 2 behavior
with the flag off.

``type_bonus`` from TECH_PLAN §5 is deferred to a later task.

An optional ``vec_only_min_final`` acts as an absolute gate for the pure-vector
path (FTS miss). When every candidate was reached only via the vector channel
and the top result's ``final`` score falls below the gate, the reranker returns
an empty list. This is the mechanism by which M3b restores the negative-case
100% target (see GROWTH_LOG M2 / M3b §5 decision B).

``scope_mismatch_penalty`` fires when an unscoped query (``query_scope is None``)
hits a project-scoped row — a cross-project-contamination signal used together
with authority to separate REL-N OOD queries from INT pure-vector positives.
"""

from __future__ import annotations

from typing import Any, Callable

from mnemo.ranking.authority import authority_multiplier

CONTRADICTION_PENALTY = 0.7
# Module-level fallback for callers that don't pass an explicit value. The
# production path reads ``MnemoConfig.scope_mismatch_penalty`` = 0.8, the
# M3b-validated value (see config.py note and docs/phase2/GROWTH_LOG.md
# M4 section on the grid-search rollback). This constant stays at 0.6 so
# standalone rerank unit tests exercise a non-neutral path distinct from
# the production default; production callers pass their own value.
SCOPE_MISMATCH_PENALTY = 0.6
# Phase 3 P3a-M3: stale-status rows get a 0.6 hard-penalty multiplier on top
# of freshness decay. See docs/phase3/TECH_PLAN.md §4.4.
STALE_PENALTY = 0.6


def _state_machine_enabled_default() -> bool:
    """Read the ``state_machine_enabled`` flag from MnemoConfig (env-aware).

    Module-level helper so unit tests that monkeypatch
    ``MNEMO_STATE_MACHINE_ENABLED`` observe the flag without having to thread
    a config through every call. Production callers of ``apply_rerank`` pass
    ``state_machine_enabled`` explicitly.
    """
    from mnemo.config import MnemoConfig

    try:
        return MnemoConfig().state_machine_enabled
    except Exception:  # noqa: BLE001 — never crash rerank on config lookup
        return True


def apply_rerank(
    fused: list[dict[str, Any]],
    *,
    authority_lookup: Callable[[int], float],
    contradiction_lookup: Callable[[int], bool],
    alpha: float = 0.1,
    vec_only_min_final: float | None = None,
    query_scope: str | None = None,
    scope_mismatch_penalty: float = SCOPE_MISMATCH_PENALTY,
    freshness_lookup: Callable[[int], float] | None = None,
    status_lookup: Callable[[int], str] | None = None,
    stale_penalty: float = STALE_PENALTY,
    state_machine_enabled: bool | None = None,
    verification_lookup: Callable[[int], float] | None = None,
    claim_type_boost: dict[str, float] | None = None,
    contradicts_edge_lookup: Callable[[int], bool] | None = None,
) -> list[dict[str, Any]]:
    """Apply authority, contradiction, and scope-mismatch rerank.

    Args:
        fused: output of ``rrf_fuse`` — each dict has ``id``, ``rrf_score``,
            ``fts_rank``, ``vec_rank``, ``source``, and (when available)
            ``scope``.
        authority_lookup: ``id -> authority score`` (>= 0).
        contradiction_lookup: ``id -> True`` iff the node has any ``contradicts``
            edge (in or out).
        alpha: authority multiplier coefficient (TECH_PLAN default 0.1).
        vec_only_min_final: optional absolute gate for pure-vector paths. When
            every surviving candidate is vec-only (FTS produced zero hits) and
            the top ``final`` score is below this threshold, return ``[]``.
        query_scope: caller's scope filter. When ``None`` the query is unscoped
            and project-scoped hits get ``scope_mismatch_penalty`` applied.
        scope_mismatch_penalty: multiplier for unscoped-query vs project-hit
            (default 0.8).
        freshness_lookup: ``id -> freshness multiplier`` (already β-floored by
            the caller, e.g. via ``batch_freshness_lookup``). When ``None`` the
            per-entry ``freshness_mult`` field is used if present, otherwise 1.0.
        status_lookup: optional ``id -> status string``. When ``None`` the
            per-entry ``status`` field is used — callers that already carry
            status on the fused dict don't need a lookup.
        stale_penalty: multiplier applied when status == ``'stale'``
            (Phase 3 P3a-M3, default ``STALE_PENALTY`` = 0.6).
        state_machine_enabled: flag override. ``None`` (default) reads the
            flag from a fresh ``MnemoConfig`` — honors ``MNEMO_STATE_MACHINE_ENABLED``
            env var. When ``False`` no stale penalty fires regardless of status.
        verification_lookup: optional ``id -> verification multiplier`` (Phase 3
            P3a-M4 feedback loop). When ``None`` the multiplier degrades to
            ``1.0`` — i.e. Phase 2 behavior with the feedback-loop flag off.
        claim_type_boost: optional ``{claim_type: multiplier}`` dict (Phase 3
            P3b-M7 context-aware rank). Each entry's ``claim_type`` is looked up
            in this dict and the result multiplied into ``final``; missing keys
            default to 1.0. A special ``"contradicts_edge"`` key (paired with
            ``contradicts_edge_lookup``) applies an additional multiplier when
            the entry has any ``contradicts`` relation edge. When ``None`` the
            multiplier degrades to 1.0 — i.e. Phase 2 behavior with the
            context-aware flag off.
        contradicts_edge_lookup: optional ``id -> True`` when the row has any
            ``contradicts`` edge. Only consulted when ``claim_type_boost``
            contains a ``"contradicts_edge"`` key (debug task_context).

    Returns:
        New list sorted by ``final_score`` desc. Input dicts are copied; the
        original ``rrf_score`` and rank fields are preserved. Each entry gains:
        - ``authority``: float
        - ``authority_mult``: float
        - ``contradiction_penalty``: float (1.0 or CONTRADICTION_PENALTY)
        - ``scope_penalty``: float (1.0 or scope_mismatch_penalty)
        - ``freshness_mult``: float (1.0 when no freshness signal is available)
        - ``stale_penalty``: float (1.0 or ``stale_penalty``)
        - ``verification_mult``: float (1.0 when no verification_lookup given)
        - ``context_boost``: float (1.0 when no claim_type_boost given)
        - ``final_score``: float
    """
    if alpha < 0:
        raise ValueError(f"alpha must be non-negative, got {alpha}")
    if scope_mismatch_penalty <= 0:
        raise ValueError(
            f"scope_mismatch_penalty must be positive, got {scope_mismatch_penalty}"
        )

    if state_machine_enabled is None:
        flag_on = _state_machine_enabled_default()
    else:
        flag_on = state_machine_enabled

    reranked: list[dict[str, Any]] = []
    for entry in fused:
        kid = entry["id"]
        authority = authority_lookup(kid)
        a_mult = authority_multiplier(authority, alpha=alpha)
        penalty = CONTRADICTION_PENALTY if contradiction_lookup(kid) else 1.0
        scope_pen = (
            scope_mismatch_penalty
            if query_scope is None and entry.get("scope") == "project"
            else 1.0
        )
        if freshness_lookup is not None:
            fresh_mult = freshness_lookup(kid)
        else:
            # Fall back to a caller-supplied per-entry value (set by the
            # service layer when a single batch lookup feeds both signals).
            fresh_mult = float(entry.get("freshness_mult", 1.0))

        if flag_on:
            if status_lookup is not None:
                status = status_lookup(kid)
            else:
                status = entry.get("status")
            stale_mult = stale_penalty if status == "stale" else 1.0
        else:
            stale_mult = 1.0

        if verification_lookup is not None:
            verif_mult = verification_lookup(kid)
        else:
            verif_mult = 1.0

        if claim_type_boost:
            ct = entry.get("claim_type")
            ctx_boost = float(claim_type_boost.get(ct, 1.0)) if ct else 1.0
            if (
                "contradicts_edge" in claim_type_boost
                and contradicts_edge_lookup is not None
                and contradicts_edge_lookup(kid)
            ):
                ctx_boost *= float(claim_type_boost["contradicts_edge"])
        else:
            ctx_boost = 1.0

        final = (
            entry["rrf_score"]
            * a_mult
            * penalty
            * scope_pen
            * fresh_mult
            * stale_mult
            * verif_mult
            * ctx_boost
        )

        new_entry = dict(entry)
        new_entry["authority"] = authority
        new_entry["authority_mult"] = a_mult
        new_entry["contradiction_penalty"] = penalty
        new_entry["scope_penalty"] = scope_pen
        new_entry["freshness_mult"] = fresh_mult
        new_entry["stale_penalty"] = stale_mult
        new_entry["verification_mult"] = verif_mult
        new_entry["context_boost"] = ctx_boost
        new_entry["final_score"] = final
        reranked.append(new_entry)

    reranked.sort(key=lambda e: e["final_score"], reverse=True)

    if vec_only_min_final is not None and reranked:
        all_vec_only = all(e.get("source") == "vec_only" for e in reranked)
        if all_vec_only and reranked[0]["final_score"] < vec_only_min_final:
            return []

    return reranked
