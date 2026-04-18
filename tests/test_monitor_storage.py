"""Unit tests for monitor storage layer (task #55).

Scope: models / retention / queries. CLI smoke-tested via typer's runner.
Red lines: no mocks — real SQLite engine with the real schema.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base
from mnemo.monitor import queries as monitor_queries
from mnemo.monitor.models import (
    AlertHistory,
    MonitorEvent,
    MonitorHealth,
    MonRuleState,
)
from mnemo.monitor.retention import cleanup_old_alerts, cleanup_old_events


@pytest_asyncio.fixture
async def monitor_session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "monitor.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys = ON"))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _evt(
    *,
    tool: str = "search",
    created_at: datetime | None = None,
    latency_ms: float = 100.0,
    status: str = "ok",
    result_meta: dict | None = None,
    session_id: str = "sess-1",
    actor: str = "agent:test",
) -> MonitorEvent:
    return MonitorEvent(
        tool_name=tool,
        params_json="{}",
        result_summary="",
        latency_ms=latency_ms,
        created_at=created_at or datetime.now(timezone.utc),
        actor=actor,
        session_id=session_id,
        status=status,
        result_meta=json.dumps(result_meta) if result_meta is not None else None,
    )


@pytest.mark.asyncio
async def test_monitor_health_roundtrip(monitor_session: AsyncSession) -> None:
    row = MonitorHealth(
        total_knowledge=100,
        active=70,
        stale=20,
        archived=10,
        avg_search_latency_ms=123.4,
        empty_search_ratio=0.12,
    )
    monitor_session.add(row)
    await monitor_session.commit()
    await monitor_session.refresh(row)
    assert row.id is not None
    assert row.total_knowledge == 100
    assert row.empty_search_ratio == pytest.approx(0.12)
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_mon_rule_state_pk_is_rule_id(monitor_session: AsyncSession) -> None:
    monitor_session.add(
        MonRuleState(
            rule_id="search.empty_streak",
            domain="search_quality",
            trigger_count=3,
            state_json='{"streak": 3}',
        )
    )
    await monitor_session.commit()
    got = await monitor_session.get(MonRuleState, "search.empty_streak")
    assert got is not None
    assert got.trigger_count == 3
    assert got.domain == "search_quality"


@pytest.mark.asyncio
async def test_alert_history_insert(monitor_session: AsyncSession) -> None:
    alert = AlertHistory(
        rule_id="search.latency_sustained",
        severity="critical",
        message="p95 > 1000ms",
    )
    monitor_session.add(alert)
    await monitor_session.commit()
    await monitor_session.refresh(alert)
    assert alert.id is not None
    assert alert.notified_at is not None


@pytest.mark.asyncio
async def test_cleanup_old_events_prunes_beyond_window(
    monitor_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    monitor_session.add_all(
        [
            _evt(created_at=now - timedelta(days=40)),
            _evt(created_at=now - timedelta(days=31)),
            _evt(created_at=now - timedelta(days=10)),
            _evt(created_at=now),
        ]
    )
    await monitor_session.commit()

    deleted = await cleanup_old_events(monitor_session, days=30)
    assert deleted == 2

    remaining = await monitor_queries.recent_events(monitor_session, limit=10)
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_cleanup_old_alerts_prunes_beyond_window(
    monitor_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    monitor_session.add_all(
        [
            AlertHistory(
                rule_id="r1",
                severity="warning",
                message="old",
                notified_at=now - timedelta(days=45),
            ),
            AlertHistory(
                rule_id="r1",
                severity="warning",
                message="fresh",
                notified_at=now - timedelta(days=1),
            ),
        ]
    )
    await monitor_session.commit()

    deleted = await cleanup_old_alerts(monitor_session, days=30)
    assert deleted == 1


@pytest.mark.asyncio
async def test_recent_events_orders_newest_first(
    monitor_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    monitor_session.add_all(
        [
            _evt(tool="search", created_at=now - timedelta(hours=2)),
            _evt(tool="get_knowledge", created_at=now - timedelta(hours=1)),
            _evt(tool="create_knowledge", created_at=now),
        ]
    )
    await monitor_session.commit()

    events = await monitor_queries.recent_events(monitor_session, limit=10)
    assert [e.tool_name for e in events] == [
        "create_knowledge",
        "get_knowledge",
        "search",
    ]


@pytest.mark.asyncio
async def test_event_counts_by_tool_respects_window(
    monitor_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    monitor_session.add_all(
        [
            _evt(tool="search", created_at=now - timedelta(hours=1)),
            _evt(tool="search", created_at=now - timedelta(hours=2)),
            _evt(tool="get_knowledge", created_at=now - timedelta(hours=1)),
            _evt(tool="search", created_at=now - timedelta(days=2)),  # outside
        ]
    )
    await monitor_session.commit()

    counts = await monitor_queries.event_counts_by_tool(monitor_session, hours=24)
    assert counts == {"search": 2, "get_knowledge": 1}


@pytest.mark.asyncio
async def test_search_empty_ratio_counts_zero_hits(
    monitor_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    monitor_session.add_all(
        [
            _evt(
                tool="search",
                created_at=now - timedelta(minutes=10),
                result_meta={"hits": 0, "query_norm": "foo"},
            ),
            _evt(
                tool="search",
                created_at=now - timedelta(minutes=20),
                result_meta={"hits": 3, "query_norm": "foo"},
            ),
            _evt(
                tool="search",
                created_at=now - timedelta(minutes=30),
                result_meta={"hits": 0, "query_norm": "bar"},
            ),
            # non-search is ignored
            _evt(
                tool="get_knowledge",
                created_at=now,
                result_meta={"hits": 0},
            ),
        ]
    )
    await monitor_session.commit()

    ratio = await monitor_queries.search_empty_ratio(monitor_session, hours=24)
    assert ratio == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_search_empty_ratio_returns_zero_when_no_events(
    monitor_session: AsyncSession,
) -> None:
    ratio = await monitor_queries.search_empty_ratio(monitor_session, hours=24)
    assert ratio == 0.0


@pytest.mark.asyncio
async def test_avg_latency_by_tool(monitor_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    monitor_session.add_all(
        [
            _evt(tool="search", latency_ms=100, created_at=now),
            _evt(tool="search", latency_ms=300, created_at=now),
            _evt(tool="get_knowledge", latency_ms=50, created_at=now),
        ]
    )
    await monitor_session.commit()

    latency = await monitor_queries.avg_latency_by_tool(monitor_session, hours=24)
    assert latency["search"] == pytest.approx(200.0)
    assert latency["get_knowledge"] == pytest.approx(50.0)
