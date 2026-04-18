"""Phase 3 P3a-M4 verification multiplier — thin ranking-side shim.

The actual formula lives in :mod:`mnemo.repository.feedback_repository` so the
read path can share it without importing the ranking layer.  This module
re-exposes it as ``verification_mult`` for callers (tests, ablation, diagnostic
CLI) that think of it as a ranking signal alongside ``authority_multiplier`` and
``freshness``.

``verification_mult(helpful, misleading)`` is pure — no DB, no config.  It
honours the three knobs declared on :class:`~mnemo.config.MnemoConfig` via
keyword args so callers can override per-query if they ever need to ablate.
"""

from __future__ import annotations

from mnemo.config import MnemoConfig
from mnemo.repository.feedback_repository import (
    DEFAULT_MISLEADING_WEIGHT,
    DEFAULT_MULT_HIGH,
    DEFAULT_MULT_LOW,
    DEFAULT_SAMPLE_FLOOR,
    compute_verification_mult,
)


def verification_mult(
    helpful: int,
    misleading: int,
    *,
    config: MnemoConfig | None = None,
    sample_floor: int | None = None,
    misleading_weight: float | None = None,
    low: float | None = None,
    high: float | None = None,
) -> float:
    """Return the verification multiplier for the given bucket counts.

    Defaults come from ``config`` when provided, else the module-level Phase 3
    defaults (sample_floor=3, misleading_weight=2.0, low=0.7, high=1.3).
    ``compute_verification_mult`` is the authoritative implementation.
    """
    if config is not None:
        if sample_floor is None:
            sample_floor = int(
                getattr(config, "feedback_sample_floor", DEFAULT_SAMPLE_FLOOR)
            )
        if misleading_weight is None:
            misleading_weight = float(
                getattr(config, "feedback_misleading_weight", DEFAULT_MISLEADING_WEIGHT)
            )
        if low is None:
            low = float(getattr(config, "verification_mult_low", DEFAULT_MULT_LOW))
        if high is None:
            high = float(getattr(config, "verification_mult_high", DEFAULT_MULT_HIGH))

    return compute_verification_mult(
        helpful,
        misleading,
        sample_floor=sample_floor if sample_floor is not None else DEFAULT_SAMPLE_FLOOR,
        misleading_weight=(
            misleading_weight
            if misleading_weight is not None
            else DEFAULT_MISLEADING_WEIGHT
        ),
        low=low if low is not None else DEFAULT_MULT_LOW,
        high=high if high is not None else DEFAULT_MULT_HIGH,
    )


__all__ = ["verification_mult"]
