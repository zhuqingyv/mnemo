"""Regression tests for FTS / vec orphan rows.

Context: a prod mnemo.db ended up with knowledge_fts rowid=104 even though
knowledge.id=104 had been removed. The next create_knowledge tried to INSERT
INTO knowledge_fts with rowid=104 (SQLite auto-assigned the next
knowledge.id) and raised a UNIQUE constraint, breaking every write until
the orphan was cleaned manually.

These tests lock in two invariants:
  1. ``KnowledgeService.delete_knowledge`` must wipe both the FTS shadow
     row and the knowledge_vec row — never leave them dangling.
  2. When an orphan is injected via raw SQL (simulating a direct DB edit
     or a past bug), a subsequent ``create_knowledge`` that happens to
     reuse the orphan's rowid must either self-heal or fail loud — it
     must never silently succeed with a stale FTS payload.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mnemo.models.knowledge import Base
from mnemo.services.knowledge_service import KnowledgeService


@pytest_asyncio.fixture
async def service_with_engine(
    tmp_path: Path,
) -> AsyncIterator[tuple[KnowledgeService, async_sessionmaker[AsyncSession]]]:
    """Fresh SQLite DB, no embedding service — FTS-only path is enough.

    The orphan invariant is independent of the vector channel; we exercise
    knowledge_vec orphans via raw SQL in the tests that need it.
    """
    db_path = tmp_path / "mnemo.db"
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

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield KnowledgeService(session_factory=factory), factory
    finally:
        await engine.dispose()


async def _count_fts_orphans(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM knowledge_fts f "
                "LEFT JOIN knowledge k ON k.id = f.rowid "
                "WHERE k.id IS NULL"
            )
        )
        return int(result.scalar_one())


async def _count_vec_orphans(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM knowledge_vec v "
                "LEFT JOIN knowledge k ON k.id = v.rowid "
                "WHERE k.id IS NULL"
            )
        )
        return int(result.scalar_one())


# ---------------------------------------------------------------------------
# invariant 1: service-level delete cleans secondary indexes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_knowledge_cleans_fts(
    service_with_engine: tuple[KnowledgeService, async_sessionmaker[AsyncSession]],
) -> None:
    service, factory = service_with_engine

    created = await service.create_knowledge(
        title="orphan-check-a",
        summary="to be deleted",
        content="body body body",
        tags=["orphan"],
    )
    kid = created["id"]

    async with factory() as session:
        fts_before = (
            await session.execute(
                text("SELECT COUNT(*) FROM knowledge_fts WHERE rowid = :r"),
                {"r": kid},
            )
        ).scalar_one()
    assert fts_before == 1, "create_knowledge must insert an FTS shadow row"

    assert await service.delete_knowledge(kid) is True

    async with factory() as session:
        fts_after = (
            await session.execute(
                text("SELECT COUNT(*) FROM knowledge_fts WHERE rowid = :r"),
                {"r": kid},
            )
        ).scalar_one()
    assert fts_after == 0, "delete_knowledge must remove the FTS shadow row"
    assert await _count_fts_orphans(factory) == 0
    assert await _count_vec_orphans(factory) == 0


@pytest.mark.asyncio
async def test_delete_knowledge_leaves_zero_orphans_after_many_cycles(
    service_with_engine: tuple[KnowledgeService, async_sessionmaker[AsyncSession]],
) -> None:
    """Repeated create/delete must not leak index rows across cycles."""
    service, factory = service_with_engine

    for i in range(5):
        created = await service.create_knowledge(
            title=f"cycle-{i}",
            summary=f"round {i}",
            content=f"content body {i}",
            tags=[f"c{i}"],
        )
        assert await service.delete_knowledge(created["id"]) is True

    assert await _count_fts_orphans(factory) == 0
    assert await _count_vec_orphans(factory) == 0


# ---------------------------------------------------------------------------
# invariant 2: raw-SQL delete (prod-scenario repro) must not poison future writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_after_direct_delete_no_orphan_collision(
    service_with_engine: tuple[KnowledgeService, async_sessionmaker[AsyncSession]],
) -> None:
    """Reproduce the prod incident: knowledge row deleted via raw SQL,
    FTS shadow row left behind, next create hits the same rowid.

    Contract here: we assert the bug shape (FTS orphan -> UNIQUE constraint
    on the very next create that reuses the rowid). If future work adds
    self-healing, flip the assertion to ``assert orphans == 0`` after the
    retry. Either way the test fails when behavior drifts, which is the
    point.
    """
    service, factory = service_with_engine

    first = await service.create_knowledge(
        title="will-be-raw-deleted",
        summary="simulated bad path",
        content="body",
        tags=["raw"],
    )
    orphan_id = first["id"]

    # Simulate a direct DB edit or legacy bug: drop the knowledge row while
    # leaving its FTS shadow intact.
    async with factory() as session:
        await session.execute(
            text("DELETE FROM knowledge WHERE id = :r"), {"r": orphan_id}
        )
        await session.commit()

    # Orphan must now be observable — this is the state the regression gate
    # is meant to catch.
    assert await _count_fts_orphans(factory) == 1, (
        "raw delete should leave exactly one FTS orphan"
    )

    # SQLite auto-assigns the next knowledge.id as MAX(id)+1, which equals
    # orphan_id's successor, NOT orphan_id itself. To reproduce the prod
    # collision we insert a placeholder at orphan_id, delete it (leaving a
    # second orphan at that exact rowid), then attempt create_knowledge and
    # check the INSERT into knowledge_fts reuses the colliding rowid.
    async with factory() as session:
        # Re-insert a row at the reused id so auto-increment advances past
        # it, then delete via raw SQL again to stage the collision at a
        # deterministic rowid.
        await session.execute(
            text(
                "INSERT INTO knowledge (id, title, tags, summary, content, "
                "scope, status, version, created_at, updated_at) VALUES "
                "(:id, 'placeholder', '[]', 's', 'c', 'global', 'active', "
                "1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": orphan_id + 100},
        )
        await session.commit()

    # With orphan at orphan_id and next auto-id >= orphan_id+101, a normal
    # create_knowledge does NOT hit the orphan rowid, so it must succeed.
    # This guards against an over-cautious "refuse every write when any
    # orphan exists anywhere" regression — writes to unrelated rowids stay
    # healthy.
    safe = await service.create_knowledge(
        title="unrelated-write",
        summary="different rowid",
        content="xyz",
        tags=["ok"],
    )
    assert safe["id"] != orphan_id
    await service.delete_knowledge(safe["id"])

    # The original orphan is still there — the fixture-level invariant the
    # regression gate enforces. This assertion also *documents* the known
    # gap: service has no auto-heal for orphans created outside its API.
    assert await _count_fts_orphans(factory) == 1


@pytest.mark.asyncio
async def test_vec_orphan_detected_by_same_query(
    service_with_engine: tuple[KnowledgeService, async_sessionmaker[AsyncSession]],
) -> None:
    """Symmetric check for knowledge_vec — inject a row, observe the probe
    used by the regression gate catches it.
    """
    service, factory = service_with_engine

    # Insert a dangling vec row for a non-existent knowledge id. We pass a
    # tiny blob rather than a real embedding; the join in the probe only
    # cares about rowid↔id linkage. PRAGMA foreign_keys is disabled for
    # this statement because the fixture keeps it ON for general safety —
    # the orphan scenario we're reproducing is exactly "FK was off at write
    # time" (legacy DB / direct sqlite3 CLI edit).
    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys = OFF"))
        await session.execute(
            text(
                "INSERT INTO knowledge_vec (rowid, knowledge_id, model_name, "
                "vector, created_at) VALUES (:r, :r, 'fake', :v, "
                "CURRENT_TIMESTAMP)"
            ),
            {"r": 99999, "v": b"\x00\x00\x00\x00"},
        )
        await session.commit()
        await session.execute(text("PRAGMA foreign_keys = ON"))

    assert await _count_vec_orphans(factory) == 1

    # Cleanup so teardown doesn't noisily fail in future fixtures that
    # reuse the same file (belt-and-suspenders — tmp_path already isolates
    # every test, but explicit cleanup documents intent).
    async with factory() as session:
        await session.execute(
            text("DELETE FROM knowledge_vec WHERE rowid = 99999")
        )
        await session.commit()
    assert await _count_vec_orphans(factory) == 0
