"""Test: degraded (FTS-only) path still applies _quality_rerank.

When the embedding service is unavailable the hybrid pipeline collapses to
FTS-only.  The bug fixed in caec19b skipped _quality_rerank on that path,
which meant stale_penalty / freshness / scope signals were silently lost.

This test creates two knowledge entries that both match the same FTS query,
marks one as stale, and asserts the stale entry is ranked below the active
one after search — proving that rerank (stale_penalty) fires on the degraded
path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
import sqlite_vec
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Base
from mnemo.services.knowledge_service import KnowledgeService


# ---- DB helpers (same pattern as test_search_auto_fallback) ---------------


def _load_sqlite_vec(dbapi_conn, _cr) -> None:
    aiosqlite_conn = getattr(dbapi_conn, "_connection", None)
    if aiosqlite_conn is None:
        dbapi_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_conn)
        finally:
            dbapi_conn.enable_load_extension(False)
        return

    def _do_load(sync_conn):
        sync_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(sync_conn)
        finally:
            sync_conn.enable_load_extension(False)

    dbapi_conn.await_(aiosqlite_conn._execute(_do_load, aiosqlite_conn._conn))


async def _init_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(
            text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec_idx "
                f"USING vec0(knowledge_id INTEGER PRIMARY KEY, "
                f"embedding FLOAT[{VECTOR_DIM}])"
            )
        )


# ---- Fixture: service with NO embedding (always degraded) ----------------


@pytest_asyncio.fixture
async def degraded_service(tmp_path: Path):
    db_path = tmp_path / "mnemo-degraded.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", echo=False
    )
    event.listen(engine.sync_engine, "connect", _load_sqlite_vec)
    await _init_schema(engine)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    # Ensure stale penalty features are on.
    config.state_machine_enabled = True
    config.freshness_enabled = True

    # No embedding_service => embedding is None => degraded path.
    service = KnowledgeService(
        session_factory=factory, config=config, embedding_service=None
    )

    # Seed two entries sharing the keyword "deployment".
    # Entry A: active.
    await service.create_knowledge(
        title="deployment guide active",
        summary="active deployment notes",
        content="Step by step deployment procedure for production.",
        tags="deployment,ops",
        scope="global",
        status="active",
    )
    # Entry B: stale.
    await service.create_knowledge(
        title="deployment guide stale",
        summary="stale deployment notes",
        content="Old deployment procedure, no longer recommended.",
        tags="deployment,legacy",
        scope="global",
        status="stale",
    )

    try:
        yield service
    finally:
        await engine.dispose()


# ---- Test -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_path_applies_rerank(
    degraded_service: KnowledgeService,
) -> None:
    """When embedding service is unavailable, FTS-only results should still
    go through _quality_rerank so stale_penalty takes effect."""
    results = await degraded_service.search("deployment", limit=10)

    # Both entries must be returned by FTS.
    assert len(results) >= 2, (
        f"expected at least 2 FTS hits for 'deployment', got {len(results)}"
    )

    # Find the positions of active vs stale entries.
    active_idx = None
    stale_idx = None
    for i, r in enumerate(results):
        if "active" in r.get("title", "").lower():
            active_idx = i
        if "stale" in r.get("title", "").lower():
            stale_idx = i

    assert active_idx is not None, "active entry not found in results"
    assert stale_idx is not None, "stale entry not found in results"

    # Stale entry must be ranked below the active entry — this proves
    # _quality_rerank fired and applied stale_penalty_multiplier (0.3).
    assert active_idx < stale_idx, (
        f"stale entry (idx={stale_idx}) should rank below active (idx={active_idx}); "
        f"rerank stale_penalty did not fire on degraded path"
    )
