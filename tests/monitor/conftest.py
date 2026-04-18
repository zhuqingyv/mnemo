"""Shared fixtures for rule tests.

Red line: no mocks. Each test gets a fresh SQLite file with the real schema
created via Base.metadata.create_all (same path as test_monitor_storage.py).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mnemo.models.knowledge import Base
from mnemo.monitor.models import MonitorEvent


@pytest_asyncio.fixture
async def rules_session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "rules.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def make_event(
    *,
    tool: str,
    created_at: datetime,
    latency_ms: float = 50.0,
    status: str = "ok",
    session_id: str | None = "sess-1",
    actor: str = "agent:test",
    args_digest: str | None = None,
    knowledge_id: int | None = None,
    result_meta: dict[str, Any] | None = None,
    params_json: str = "{}",
) -> MonitorEvent:
    ts = created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return MonitorEvent(
        tool_name=tool,
        params_json=params_json,
        result_summary="",
        latency_ms=latency_ms,
        created_at=ts,
        actor=actor,
        session_id=session_id,
        status=status,
        args_digest=args_digest,
        knowledge_id=knowledge_id,
        result_meta=json.dumps(result_meta) if result_meta is not None else None,
    )
