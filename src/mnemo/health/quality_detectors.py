"""P2 quality detectors — docs/phase5/HEALTH_CHECK_DESIGN.md §1/§2/§3.

Trigger-scoped checks that emit HealthTask rows. No full scans; no commits.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.health.task_store import HealthTask, add_task
from mnemo.models.knowledge import Knowledge, KnowledgeTag, Relation


_P2_HIGH_SIMILARITY_THRESHOLD = 0.92
_P2_MIN_CONTENT_CHARS = 50
_P2_EDGE_WEIGHT_FLOOR = 0.1
_P2_EDGE_MIN_AGE_DAYS = 7
_AUTO_RELATED = "auto_related"


def _tags_of(tags_json: str | None) -> list[str]:
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
    except json.JSONDecodeError:
        return []
    return [str(t) for t in data if t] if isinstance(data, list) else []


def _emit(**kwargs: Any) -> HealthTask:
    t = HealthTask(**kwargs)
    add_task(t)
    return t


async def detect_high_similarity(
    session: AsyncSession,
    knowledge_id: int,
    semantic_similar: list[dict[str, Any]] | None,
) -> list[HealthTask]:
    """P2-1: cosine >= 0.92 with another active row, surfaced from write_gate."""
    if not semantic_similar:
        return []
    row = await session.get(Knowledge, knowledge_id)
    if row is None:
        return []
    out: list[HealthTask] = []
    for cand in semantic_similar:
        cos = float(cand.get("cosine", 0.0))
        if cos < _P2_HIGH_SIMILARITY_THRESHOLD:
            continue
        cid = int(cand["id"])
        out.append(_emit(
            problem_type="P2-1_high_similarity", priority=0.4,
            target_ids=sorted({knowledge_id, cid}), action="merge_or_link",
            description=f"#{knowledge_id} is {cos:.2f} cosine-similar to #{cid}",
            project_name=row.project_name, tags=_tags_of(row.tags),
        ))
    return out


async def detect_island_knowledge(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P2-2: active knowledge with zero incoming/outgoing relation edges."""
    row = await session.get(Knowledge, knowledge_id)
    if row is None or row.status != "active":
        return []
    stmt = select(func.count(Relation.id)).where(
        (Relation.source_id == knowledge_id) | (Relation.target_id == knowledge_id)
    )
    if int((await session.execute(stmt)).scalar_one() or 0) > 0:
        return []
    return [_emit(
        problem_type="P2-2_island", priority=0.3, target_ids=[knowledge_id],
        action="suggest_relations",
        description=f"#{knowledge_id} has no relations — suggest linking",
        project_name=row.project_name, tags=_tags_of(row.tags),
    )]


async def detect_weak_evidence(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P2-3: fact without source, content < 50 chars, or empty summary."""
    row = await session.get(Knowledge, knowledge_id)
    if row is None or row.status != "active":
        return []
    content = row.content or ""
    if len(content) < _P2_MIN_CONTENT_CHARS:
        reason = "content_too_short"
    elif row.claim_type == "fact" and not (row.source and row.source.strip()):
        reason = "no_source_for_fact"
    elif not (row.summary and row.summary.strip()):
        reason = "empty_summary"
    else:
        return []
    return [_emit(
        problem_type="P2-3_weak_evidence", priority=0.35,
        target_ids=[knowledge_id], action="strengthen_evidence",
        description=f"#{knowledge_id} weak evidence ({reason})",
        project_name=row.project_name, tags=_tags_of(row.tags),
    )]


async def detect_low_weight_edge(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P2-4: auto_related edges with weight < 0.1 older than 7 days."""
    stmt = select(Relation.id, Relation.source_id, Relation.target_id).where(
        (Relation.source_id == knowledge_id) | (Relation.target_id == knowledge_id),
        Relation.relation_type == _AUTO_RELATED,
        Relation.weight < _P2_EDGE_WEIGHT_FLOOR,
        Relation.created_at < func.datetime("now", f"-{_P2_EDGE_MIN_AGE_DAYS} days"),
    )
    rows = (await session.execute(stmt)).all()
    return [_emit(
        problem_type="P2-4_low_weight_edge", priority=0.25,
        target_ids=sorted({int(src), int(tgt)}),
        action="drop_or_downgrade_edge",
        description=f"edge #{edge_id} {src}↔{tgt} weight<0.1 for >7d",
    ) for edge_id, src, tgt in rows]


async def detect_search_blind_spot(
    session: AsyncSession, query: str, hit_count: int
) -> list[HealthTask]:
    """P2-5: zero-hit search — the query itself is the signal."""
    if hit_count > 0:
        return []
    q = (query or "").strip()
    if not q:
        return []
    return [_emit(
        problem_type="P2-5_search_blind_spot", priority=0.45, target_ids=[],
        action="create_knowledge_for_query",
        description=f"query {q!r} returned zero hits", tags=[q],
    )]


async def detect_feedback_gap(
    session: AsyncSession, knowledge_ids: list[int]
) -> list[HealthTask]:
    """P2-6: search hits served but no feedback loop yet — remind next agent."""
    targets = [int(k) for k in knowledge_ids if k is not None]
    if not targets:
        return []
    return [_emit(
        problem_type="P2-6_feedback_gap", priority=0.2,
        target_ids=sorted(targets), action="remind_feedback",
        description=f"served {len(targets)} id(s) — remind feedback",
    )]


async def detect_tag_inconsistency(
    session: AsyncSession,
    tags: list[str],
    exclude_knowledge_id: int | None = None,
) -> list[HealthTask]:
    """P2-7: incoming tag collides with an existing tag only after LOWER fold.

    ``exclude_knowledge_id`` skips the row we just wrote (its own tag rows).
    """
    incoming = [t for t in (tags or []) if t]
    if not incoming:
        return []
    lowered = {t.lower(): t for t in incoming}
    stmt = select(KnowledgeTag.tag).where(
        func.lower(KnowledgeTag.tag).in_(list(lowered.keys()))
    )
    if exclude_knowledge_id is not None:
        stmt = stmt.where(KnowledgeTag.knowledge_id != exclude_knowledge_id)
    existing = [r[0] for r in (await session.execute(stmt)).all()]
    if not existing:
        return []
    out: list[HealthTask] = []
    seen: set[tuple[str, str]] = set()
    for tag in existing:
        inc = lowered.get(tag.lower())
        if inc is None or inc == tag:
            continue
        key = tuple(sorted([inc, tag]))
        if key in seen:
            continue
        seen.add(key)
        out.append(_emit(
            problem_type="P2-7_tag_inconsistency", priority=0.3,
            target_ids=[], action="normalize_tag",
            description=f"tag {inc!r} vs existing {tag!r} (same LOWER)",
            tags=[inc, tag],
        ))
    return out


