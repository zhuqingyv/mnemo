"""Authority score — pure function, per TECH_PLAN §5.

The formula counts typed *incoming* relations only:

    authority = log(1 + 2*incoming_supersedes
                      + 1.5*incoming_refines
                      + 1.0*incoming_derived_from)

Other relation types (related/wikilink/depends_on/alternative_to/example_of)
do not feed authority — they carry different semantics and are handled via
type_bonus (out of scope for M3b task #2).

The DB side returns a plain count dict; this module never touches a session.
"""

from __future__ import annotations

import math
from typing import Mapping

SUPERSEDES_WEIGHT = 2.0
REFINES_WEIGHT = 1.5
DERIVED_FROM_WEIGHT = 1.0

AUTHORITY_INCOMING_TYPES: tuple[str, ...] = (
    "supersedes",
    "refines",
    "derived_from",
)


def authority_score(incoming_counts: Mapping[str, int]) -> float:
    """Compute authority from a mapping of relation_type -> incoming count.

    Unknown keys are ignored. Missing keys are treated as 0. The return value
    is always >= 0 and typically in [0, 3] for normal nodes, up to ~4 for
    extreme hotspots (per TECH_PLAN §4).
    """
    weighted = (
        SUPERSEDES_WEIGHT * incoming_counts.get("supersedes", 0)
        + REFINES_WEIGHT * incoming_counts.get("refines", 0)
        + DERIVED_FROM_WEIGHT * incoming_counts.get("derived_from", 0)
    )
    if weighted < 0:
        raise ValueError(f"negative incoming count: {incoming_counts!r}")
    return math.log(1.0 + weighted)


def authority_multiplier(authority: float, alpha: float = 0.1) -> float:
    """Convert an authority score to a RRF multiplier: ``1 + alpha*authority``.

    ``alpha`` defaults to 0.1 per TECH_PLAN §4. Valid range is [0.05, 0.15].
    """
    if authority < 0:
        raise ValueError(f"authority must be non-negative, got {authority}")
    if alpha < 0:
        raise ValueError(f"alpha must be non-negative, got {alpha}")
    return 1.0 + alpha * authority
