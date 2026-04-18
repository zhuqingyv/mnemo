"""Rolling retention for monitor tables (design §3.6).

Called daily by the detector runner. Safe to run concurrently with collector
writes — SQLite WAL lets the DELETE happen without blocking INSERTs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from mnemo.monitor.models import AlertHistory, MonitorEvent


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def cleanup_old_events(session: AsyncSession, days: int = 30) -> int:
    """Delete monitor_event rows older than ``days`` days. Returns row count."""
    cutoff = _cutoff(days)
    result = await session.execute(
        delete(MonitorEvent).where(MonitorEvent.created_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0


async def cleanup_old_alerts(session: AsyncSession, days: int = 30) -> int:
    """Delete alert_history rows older than ``days`` days. Returns row count."""
    cutoff = _cutoff(days)
    result = await session.execute(
        delete(AlertHistory).where(AlertHistory.notified_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0


__all__ = ["cleanup_old_events", "cleanup_old_alerts"]
