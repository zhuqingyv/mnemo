"""P1 problem detectors — docs/phase5/HEALTH_CHECK_DESIGN.md §1-§3.

Scoped per-knowledge_id (no full scans). Safe to await off the triggering
session; callers wrap in try/except so detection failure can't break writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.health.task_store import HealthTask
from mnemo.models.knowledge import (
    Knowledge,
    KnowledgeEvent,
    KnowledgeVec,
    Relation,
)
from mnemo.repository.feedback_repository import FEEDBACK_EVENT_TYPE


P1_MISLEADING_THRESHOLD = 3
P1_STALE_DAYS = 30


def _tags(s: str | None) -> list[str]:
    if not s:
        return []
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return []
    return [str(t) for t in data] if isinstance(data, list) else []


async def _row(session: AsyncSession, kid: int) -> Knowledge | None:
    return (
        await session.execute(select(Knowledge).where(Knowledge.id == kid))
    ).scalar_one_or_none()


async def detect_high_misleading(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P1-1: last N feedback events all 'misleading'."""
    row = await _row(session, knowledge_id)
    if row is None or row.status != "active":
        return []
    payloads = (await session.execute(
        select(KnowledgeEvent.payload_json)
        .where(
            KnowledgeEvent.knowledge_id == knowledge_id,
            KnowledgeEvent.event_type == FEEDBACK_EVENT_TYPE,
        )
        .order_by(KnowledgeEvent.created_at.desc())
        .limit(P1_MISLEADING_THRESHOLD)
    )).scalars().all()
    if len(payloads) < P1_MISLEADING_THRESHOLD:
        return []
    for p in payloads:
        try:
            if json.loads(p or "{}").get("signal") != "misleading":
                return []
        except json.JSONDecodeError:
            return []
    return [HealthTask(
        problem_type="P1-1", priority=0.9, target_ids=[knowledge_id],
        project_name=row.project_name, tags=_tags(row.tags),
        action="archive_knowledge",
        description=f"#{knowledge_id} '{row.title}' 连续 >= {P1_MISLEADING_THRESHOLD} 次 misleading，建议归档。",
    )]


async def detect_unresolved_contradiction(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P1-2: contradicts edge with both endpoints active."""
    row = await _row(session, knowledge_id)
    if row is None or row.status != "active":
        return []
    edges = (await session.execute(
        select(Relation.source_id, Relation.target_id).where(
            Relation.relation_type == "contradicts",
            (Relation.source_id == knowledge_id) | (Relation.target_id == knowledge_id),
        )
    )).all()
    others = {s if t == knowledge_id else t for s, t in edges} - {knowledge_id}
    if not others:
        return []
    status_rows = (await session.execute(
        select(Knowledge.id, Knowledge.status).where(Knowledge.id.in_(others))
    )).all()
    active = [k for k, st in status_rows if st == "active"]
    if not active:
        return []
    return [HealthTask(
        problem_type="P1-2", priority=0.85,
        target_ids=sorted({knowledge_id, *active}),
        project_name=row.project_name, tags=_tags(row.tags),
        action="update_knowledge",
        description=f"#{knowledge_id} 与 {active} 存在未解决 contradicts。",
    )]


async def detect_hash_duplicate(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P1-3: same content_hash, both active — merge candidate."""
    row = await _row(session, knowledge_id)
    if row is None or row.status != "active" or not row.content_hash:
        return []
    dups = (await session.execute(
        select(Knowledge.id).where(
            Knowledge.content_hash == row.content_hash,
            Knowledge.status == "active",
            Knowledge.id != knowledge_id,
        )
    )).scalars().all()
    if not dups:
        return []
    return [HealthTask(
        problem_type="P1-3", priority=0.8,
        target_ids=sorted([knowledge_id, *dups]),
        project_name=row.project_name, tags=_tags(row.tags),
        action="archive_knowledge",
        description=f"#{knowledge_id} 与 {list(dups)} content_hash 相同，建议合并。",
    )]


async def detect_missing_vector(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P1-4: active knowledge with no KnowledgeVec row."""
    row = await _row(session, knowledge_id)
    if row is None or row.status != "active":
        return []
    count = (await session.execute(
        select(func.count(KnowledgeVec.id)).where(
            KnowledgeVec.knowledge_id == knowledge_id
        )
    )).scalar_one()
    if count:
        return []
    return [HealthTask(
        problem_type="P1-4", priority=0.8, target_ids=[knowledge_id],
        project_name=row.project_name, tags=_tags(row.tags),
        action="update_knowledge",
        description=f"#{knowledge_id} '{row.title}' 缺向量，update 一次触发重建。",
    )]


async def detect_zombie_stale(
    session: AsyncSession, knowledge_id: int
) -> list[HealthTask]:
    """P1-5: status='stale' and updated_at older than P1_STALE_DAYS."""
    row = await _row(session, knowledge_id)
    if row is None or row.status != "stale" or row.updated_at is None:
        return []
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if updated > datetime.now(timezone.utc) - timedelta(days=P1_STALE_DAYS):
        return []
    return [HealthTask(
        problem_type="P1-5", priority=0.8, target_ids=[knowledge_id],
        project_name=row.project_name, tags=_tags(row.tags),
        action="archive_knowledge",
        description=f"#{knowledge_id} '{row.title}' stale 超 {P1_STALE_DAYS} 天，归档或 update 重新激活。",
    )]
