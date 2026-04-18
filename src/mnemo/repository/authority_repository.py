"""DB access for M3b authority / contradiction signals.

Separated from ``mnemo.ranking.authority`` (pure math) so the query-time
rerank path can batch-lookup these values, and the write path can recompute
and persist them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.models.knowledge import KnowledgeMeta, Relation
from mnemo.ranking.authority import AUTHORITY_INCOMING_TYPES, authority_score

AUTHORITY_META_KEY = "authority_score"


async def incoming_counts_by_type(
    session: AsyncSession,
    knowledge_id: int,
) -> dict[str, int]:
    """Count incoming relations grouped by relation_type for one node.

    Only the types that feed authority are returned — callers that want the
    full histogram should use a raw query.
    """
    stmt = select(Relation.relation_type).where(
        and_(
            Relation.target_id == knowledge_id,
            Relation.relation_type.in_(AUTHORITY_INCOMING_TYPES),
        )
    )
    result = await session.execute(stmt)
    counts: dict[str, int] = defaultdict(int)
    for (rtype,) in result.all():
        counts[rtype] += 1
    return dict(counts)


async def batch_incoming_counts(
    session: AsyncSession,
    knowledge_ids: Iterable[int],
) -> dict[int, dict[str, int]]:
    """Same as ``incoming_counts_by_type`` but for many ids in one query.

    Returns ``{kid: {relation_type: count}}``. Missing ids map to ``{}``.
    """
    ids = list(knowledge_ids)
    if not ids:
        return {}
    stmt = select(Relation.target_id, Relation.relation_type).where(
        and_(
            Relation.target_id.in_(ids),
            Relation.relation_type.in_(AUTHORITY_INCOMING_TYPES),
        )
    )
    result = await session.execute(stmt)
    out: dict[int, dict[str, int]] = {kid: {} for kid in ids}
    for target_id, rtype in result.all():
        bucket = out.setdefault(target_id, {})
        bucket[rtype] = bucket.get(rtype, 0) + 1
    return out


async def has_contradiction(session: AsyncSession, knowledge_id: int) -> bool:
    """True iff the node has any incoming or outgoing ``contradicts`` edge."""
    stmt = (
        select(Relation.id)
        .where(
            Relation.relation_type == "contradicts",
            or_(
                Relation.source_id == knowledge_id,
                Relation.target_id == knowledge_id,
            ),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def batch_contradiction_flags(
    session: AsyncSession,
    knowledge_ids: Iterable[int],
) -> dict[int, bool]:
    """Return ``{kid: has_contradicts_edge}`` for the given ids."""
    ids = list(knowledge_ids)
    if not ids:
        return {}
    stmt = select(Relation.source_id, Relation.target_id).where(
        and_(
            Relation.relation_type == "contradicts",
            or_(
                Relation.source_id.in_(ids),
                Relation.target_id.in_(ids),
            ),
        )
    )
    result = await session.execute(stmt)
    flags: dict[int, bool] = {kid: False for kid in ids}
    id_set = set(ids)
    for src, tgt in result.all():
        if src in id_set:
            flags[src] = True
        if tgt in id_set:
            flags[tgt] = True
    return flags


async def get_stored_authority(
    session: AsyncSession,
    knowledge_id: int,
) -> float | None:
    """Read the cached authority_score from KnowledgeMeta, or None if absent."""
    stmt = select(KnowledgeMeta.value).where(
        and_(
            KnowledgeMeta.knowledge_id == knowledge_id,
            KnowledgeMeta.key == AUTHORITY_META_KEY,
        )
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    try:
        return float(row)
    except (TypeError, ValueError):
        return None


async def batch_stored_authority(
    session: AsyncSession,
    knowledge_ids: Iterable[int],
) -> dict[int, float]:
    """Batch variant. Missing / unparsable rows are omitted."""
    ids = list(knowledge_ids)
    if not ids:
        return {}
    stmt = select(KnowledgeMeta.knowledge_id, KnowledgeMeta.value).where(
        and_(
            KnowledgeMeta.knowledge_id.in_(ids),
            KnowledgeMeta.key == AUTHORITY_META_KEY,
        )
    )
    result = await session.execute(stmt)
    out: dict[int, float] = {}
    for kid, value in result.all():
        try:
            out[kid] = float(value)
        except (TypeError, ValueError):
            continue
    return out


async def batch_authority_and_contradiction(
    session: AsyncSession,
    knowledge_ids: Iterable[int],
) -> tuple[dict[int, float], dict[int, bool]]:
    """Fetch authority + contradiction signals for many ids.

    Both signals feed the rerank side-by-side, so issuing them as one batch
    halves the round-trip overhead. Returns ``(authority_map, contradiction_map)``
    — missing authority rows are omitted; contradiction defaults False. Used
    by the hot search path (M4 task #5 latency cut).
    """
    ids = list(knowledge_ids)
    if not ids:
        return {}, {}

    meta_stmt = select(KnowledgeMeta.knowledge_id, KnowledgeMeta.value).where(
        and_(
            KnowledgeMeta.knowledge_id.in_(ids),
            KnowledgeMeta.key == AUTHORITY_META_KEY,
        )
    )
    rel_stmt = select(Relation.source_id, Relation.target_id).where(
        and_(
            Relation.relation_type == "contradicts",
            or_(Relation.source_id.in_(ids), Relation.target_id.in_(ids)),
        )
    )

    meta_rows = (await session.execute(meta_stmt)).all()
    rel_rows = (await session.execute(rel_stmt)).all()

    auth: dict[int, float] = {}
    for kid, value in meta_rows:
        try:
            auth[kid] = float(value)
        except (TypeError, ValueError):
            continue

    id_set = set(ids)
    contra: dict[int, bool] = {kid: False for kid in ids}
    for src, tgt in rel_rows:
        if src in id_set:
            contra[src] = True
        if tgt in id_set:
            contra[tgt] = True

    return auth, contra


async def recompute_and_store_authority(
    session: AsyncSession,
    knowledge_id: int,
) -> float:
    """Recompute authority from live relations and upsert to KnowledgeMeta.

    Returns the new score. Caller is responsible for the transaction — this
    function does not commit.
    """
    counts = await incoming_counts_by_type(session, knowledge_id)
    score = authority_score(counts)

    existing = await session.execute(
        select(KnowledgeMeta).where(
            and_(
                KnowledgeMeta.knowledge_id == knowledge_id,
                KnowledgeMeta.key == AUTHORITY_META_KEY,
            )
        )
    )
    row = existing.scalar_one_or_none()
    serialized = json.dumps(score)
    if row is None:
        session.add(
            KnowledgeMeta(
                knowledge_id=knowledge_id,
                key=AUTHORITY_META_KEY,
                value=serialized,
            )
        )
    else:
        row.value = serialized
    return score
