"""Unit tests for archive_service (task #21).

Direct service-level tests that bypass MCP / CLI — they pin the contract
for ``archive_knowledge`` / ``unarchive_knowledge`` at the function
boundary: status transitions, event rows, flag gating, error shapes.

Project red lines:
  - no mocks: every test runs against a real aiosqlite DB with the full
    schema created from Base.metadata.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base, Knowledge, KnowledgeEvent
from mnemo.repository import knowledge_repository as kr
from mnemo.services import archive_service as asrv


pytestmark = pytest.mark.phase3


@pytest_asyncio.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "archive.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, "
                "knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession, **overrides) -> Knowledge:
    """Create a minimal active Knowledge row."""
    return await kr.create(
        session,
        title=overrides.get("title", "Sample"),
        summary=overrides.get("summary", "sample summary"),
        content=overrides.get("content", "sample body " * 10),
        tags=overrides.get("tags"),
        scope=overrides.get("scope", "global"),
        project_name=overrides.get("project_name"),
        session_id=overrides.get("session_id"),
        source=overrides.get("source"),
        claim_type=overrides.get("claim_type"),
        status=overrides.get("status", "active"),
    )


async def _fetch_events(
    session: AsyncSession, knowledge_id: int, event_type: str
) -> list[KnowledgeEvent]:
    result = await session.execute(
        select(KnowledgeEvent).where(
            KnowledgeEvent.knowledge_id == knowledge_id,
            KnowledgeEvent.event_type == event_type,
        )
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# archive_knowledge
# ---------------------------------------------------------------------------


async def test_archive_flips_status_and_records_event(session_factory) -> None:
    async with session_factory() as session:
        row = await _seed(session)
        await session.commit()
        kid = row.id

    async with session_factory() as session:
        result = await asrv.archive_knowledge(session, kid, reason="obsolete")

    assert result["success"] is True
    assert "archived_at" in result

    async with session_factory() as session:
        refreshed = await kr.get_by_id(session, kid)
        assert refreshed is not None
        assert refreshed.status == "archived"

        events = await _fetch_events(session, kid, "archived")
        assert len(events) == 1
        import json as _json
        payload = _json.loads(events[0].payload_json)
        assert payload == {"reason": "obsolete"}


async def test_archive_without_reason_stores_null(session_factory) -> None:
    async with session_factory() as session:
        row = await _seed(session, title="NoReason")
        await session.commit()
        kid = row.id

    async with session_factory() as session:
        result = await asrv.archive_knowledge(session, kid)

    assert result["success"] is True

    async with session_factory() as session:
        events = await _fetch_events(session, kid, "archived")
        assert len(events) == 1
        import json as _json
        assert _json.loads(events[0].payload_json) == {"reason": None}


async def test_archive_missing_id_returns_not_found(session_factory) -> None:
    async with session_factory() as session:
        result = await asrv.archive_knowledge(session, 999_999)

    assert result == {"success": False, "reason": "not_found"}


async def test_archive_already_archived_returns_error(session_factory) -> None:
    async with session_factory() as session:
        row = await _seed(session, title="Dup")
        await session.commit()
        kid = row.id

    async with session_factory() as session:
        first = await asrv.archive_knowledge(session, kid, reason="first")
        assert first["success"] is True

    async with session_factory() as session:
        second = await asrv.archive_knowledge(session, kid, reason="second")

    assert second == {"success": False, "reason": "already_archived"}

    async with session_factory() as session:
        events = await _fetch_events(session, kid, "archived")
        # Only the successful archive emitted an event.
        assert len(events) == 1


async def test_archive_flag_off_returns_feature_disabled(session_factory) -> None:
    cfg = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    cfg.state_machine_enabled = False

    async with session_factory() as session:
        row = await _seed(session, title="FlagOff")
        await session.commit()
        kid = row.id

    async with session_factory() as session:
        result = await asrv.archive_knowledge(session, kid, config=cfg)

    assert result == {"success": False, "reason": "feature_disabled"}

    async with session_factory() as session:
        refreshed = await kr.get_by_id(session, kid)
        assert refreshed is not None
        assert refreshed.status == "active"
        assert await _fetch_events(session, kid, "archived") == []


# ---------------------------------------------------------------------------
# unarchive_knowledge
# ---------------------------------------------------------------------------


async def test_unarchive_restores_active_status(session_factory) -> None:
    async with session_factory() as session:
        row = await _seed(session, title="ToRestore")
        await session.commit()
        kid = row.id

    async with session_factory() as session:
        await asrv.archive_knowledge(session, kid, reason="temp")

    async with session_factory() as session:
        result = await asrv.unarchive_knowledge(session, kid)

    assert result == {"success": True}

    async with session_factory() as session:
        refreshed = await kr.get_by_id(session, kid)
        assert refreshed is not None
        assert refreshed.status == "active"


async def test_unarchive_missing_id_returns_not_found(session_factory) -> None:
    async with session_factory() as session:
        result = await asrv.unarchive_knowledge(session, 42)
    assert result == {"success": False, "reason": "not_found"}


async def test_unarchive_non_archived_rejected(session_factory) -> None:
    async with session_factory() as session:
        row = await _seed(session, title="Active")
        await session.commit()
        kid = row.id

    async with session_factory() as session:
        result = await asrv.unarchive_knowledge(session, kid)
    assert result == {"success": False, "reason": "not_archived"}


async def test_unarchive_flag_off_returns_feature_disabled(
    session_factory,
) -> None:
    cfg = MnemoConfig(_env_file=None)  # type: ignore[call-arg]

    async with session_factory() as session:
        row = await _seed(session, title="FlagOffUnarchive")
        await session.commit()
        kid = row.id

    # Archive first with flag on so we have an archived row.
    async with session_factory() as session:
        await asrv.archive_knowledge(session, kid, reason="x", config=cfg)

    cfg.state_machine_enabled = False
    async with session_factory() as session:
        result = await asrv.unarchive_knowledge(session, kid, config=cfg)
    assert result == {"success": False, "reason": "feature_disabled"}

    async with session_factory() as session:
        refreshed = await kr.get_by_id(session, kid)
        assert refreshed is not None
        assert refreshed.status == "archived"
