"""Freshness time-decay multiplier for rerank (Phase 3 P3a-M2).

Pure math — no DB, no I/O. Two layers:

1. ``freshness_multiplier(age_days, claim_type)`` — raw exp(-λ·age) with
   per-claim-type λ from tech_research §4.2. Defensive on bad inputs:
   negative age clamped to 0, unknown / ``None`` claim_type falls back to
   ``fact`` (most conservative decay).
2. ``rerank_freshness_multiplier(age_days, claim_type, beta)`` — wraps the
   raw decay with a β floor so the oldest items still get a non-zero
   multiplier: ``β + (1-β)·freshness``.
3. ``freshness_multiplier_for(age_days, claim_type, config)`` — config-aware
   entry point used by the repository / rerank glue. Respects
   ``config.freshness_enabled`` (off → neutral 1.0) and reads λ / β from
   ``MnemoConfig`` so runtime tuning doesn't require code changes.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

LAMBDA_BY_CLAIM_TYPE: dict[str, float] = {
    "fact": 0.003,
    "decision": 0.007,
    "procedure": 0.015,
    "hypothesis": 0.02,
}
DEFAULT_CLAIM_TYPE = "fact"
DEFAULT_BETA = 0.3


def _resolve_lambda(
    claim_type: str | None,
    lambda_table: dict[str, float] = LAMBDA_BY_CLAIM_TYPE,
) -> float:
    if claim_type is None:
        return lambda_table[DEFAULT_CLAIM_TYPE]
    return lambda_table.get(claim_type, lambda_table[DEFAULT_CLAIM_TYPE])


def freshness_multiplier(age_days: float, claim_type: str | None) -> float:
    """Raw freshness decay: ``exp(-λ · max(age_days, 0))``."""
    age = age_days if age_days > 0 else 0.0
    lam = _resolve_lambda(claim_type)
    return math.exp(-lam * age)


def rerank_freshness_multiplier(
    age_days: float,
    claim_type: str | None,
    beta: float = DEFAULT_BETA,
) -> float:
    """Freshness multiplier with β floor: ``β + (1-β)·freshness``.

    Guarantees output ≥ β even as age → ∞, so very old items are penalized
    but not eliminated.
    """
    return beta + (1.0 - beta) * freshness_multiplier(age_days, claim_type)


def freshness_multiplier_for(
    age_days: float,
    claim_type: str | None,
    config: Any,
) -> float:
    """Config-aware raw freshness for repository / rerank glue.

    Returns 1.0 (neutral) when ``config.freshness_enabled`` is False so the
    feature flag cleanly disables the multiplier without touching callers.
    Reads λ from ``config.freshness_lambda_by_claim_type`` when present,
    otherwise falls back to the module-level default table.
    """
    if not getattr(config, "freshness_enabled", True):
        return 1.0

    lambda_table = getattr(
        config, "freshness_lambda_by_claim_type", LAMBDA_BY_CLAIM_TYPE
    )
    age = age_days if age_days > 0 else 0.0
    lam = _resolve_lambda(claim_type, lambda_table)
    return math.exp(-lam * age)


def _age_days_from(updated_at: datetime | None, now: datetime) -> float:
    if updated_at is None:
        return 0.0
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    delta = now - updated_at
    return delta.total_seconds() / 86400.0


def batch_freshness_lookup(
    rows: Mapping[int, tuple[datetime | None, str | None]],
    config: Any,
    *,
    now: datetime | None = None,
) -> dict[int, float]:
    """Compute rerank-ready freshness multipliers for a batch of rows.

    ``rows`` is the output of
    ``knowledge_repository.batch_updated_at_and_claim_type``:
    ``{knowledge_id: (updated_at, claim_type)}``. Returns the β-floored
    multiplier ready to plug into ``apply_rerank(freshness_lookup=...)``.

    When ``config.freshness_enabled`` is False, returns ``{id: 1.0}`` for
    every row so callers can wire the result unconditionally — the rerank
    layer decides whether to pass it in at all based on the same flag.
    """
    flag_on = getattr(config, "freshness_enabled", True)
    if not flag_on:
        return {kid: 1.0 for kid in rows}

    beta = getattr(config, "freshness_floor_beta", DEFAULT_BETA)
    now = now or datetime.now(timezone.utc)

    out: dict[int, float] = {}
    for kid, (updated_at, claim_type) in rows.items():
        age = _age_days_from(updated_at, now)
        raw = freshness_multiplier_for(age, claim_type, config)
        out[kid] = beta + (1.0 - beta) * raw
    return out
