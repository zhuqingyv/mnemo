"""Basic query interface for monitor data.

Consumers: CLI ``mnemo monitor stats`` (task #55), detector agent prototypes.
All windows are inclusive of ``now - hours`` → ``now`` and computed in UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.monitor.models import MonitorEvent


def _window_start(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


async def recent_events(
    session: AsyncSession, limit: int = 100
) -> list[MonitorEvent]:
    """Most recent ``limit`` events, newest first."""
    stmt = (
        select(MonitorEvent)
        .order_by(MonitorEvent.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def event_counts_by_tool(
    session: AsyncSession, hours: int = 24
) -> dict[str, int]:
    """Tool-call count within the window, keyed by tool_name."""
    stmt = (
        select(MonitorEvent.tool_name, func.count(MonitorEvent.id))
        .where(MonitorEvent.created_at >= _window_start(hours))
        .group_by(MonitorEvent.tool_name)
    )
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def search_empty_ratio(session: AsyncSession, hours: int = 24) -> float:
    """Share of search events where ``result_meta`` reports zero hits.

    Empty is defined as ``result_meta`` containing ``"hits": 0`` (search tool
    contract, design §2.1). Falls back to 0.0 when no search events exist —
    a lack of data is not "everything is empty".
    """
    window = _window_start(hours)
    total_stmt = select(func.count(MonitorEvent.id)).where(
        MonitorEvent.tool_name == "search",
        MonitorEvent.created_at >= window,
    )
    empty_stmt = select(func.count(MonitorEvent.id)).where(
        MonitorEvent.tool_name == "search",
        MonitorEvent.created_at >= window,
        MonitorEvent.result_meta.like('%"hits": 0%'),
    )
    total = int((await session.execute(total_stmt)).scalar_one() or 0)
    if total == 0:
        return 0.0
    empty = int((await session.execute(empty_stmt)).scalar_one() or 0)
    return empty / total


async def avg_latency_by_tool(
    session: AsyncSession, hours: int = 24
) -> dict[str, float]:
    """Mean latency in ms per tool within the window."""
    stmt = (
        select(
            MonitorEvent.tool_name,
            func.avg(MonitorEvent.latency_ms),
        )
        .where(MonitorEvent.created_at >= _window_start(hours))
        .group_by(MonitorEvent.tool_name)
    )
    result = await session.execute(stmt)
    out: dict[str, float] = {}
    for name, avg in result.all():
        out[name] = float(avg) if avg is not None else 0.0
    return out


__all__ = [
    "recent_events",
    "event_counts_by_tool",
    "search_empty_ratio",
    "avg_latency_by_tool",
]
