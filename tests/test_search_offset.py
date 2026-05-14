"""Tests for search offset pagination.

Two layers:
1. Unit test for ``fts_search(offset=...)`` — no Ollama, in-memory SQLite.
2. Integration test for ``KnowledgeService.search(offset=...)`` — requires Ollama.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import requests
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import sqlite_vec

from mnemo.config import MnemoConfig
from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Base
from mnemo.repository import knowledge_repository as kr, search_repository as sr
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services.knowledge_service import KnowledgeService


# ---------------------------------------------------------------------------
# Unit test — fts_search offset, in-memory SQLite.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fts_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts "
                "USING fts5(title, summary, content, tags, knowledge_id UNINDEXED)"
            )
        )
        await conn.execute(text("PRAGMA foreign_keys = ON"))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("PRAGMA foreign_keys = ON"))
        yield s

    await engine.dispose()


@pytest.mark.asyncio
async def test_fts_search_offset_zero_returns_first_page(
    fts_session: AsyncSession,
) -> None:
    """offset=0 returns the first N results in bm25 order."""
    # Seed 3 entries with the same distinctive word so FTS ranks by bm25.
    for i in range(3):
        await kr.create(
            fts_session,
            title=f"offset test entry {i}",
            summary="pagination",
            content=f"offset pagination test item number {i}",
            tags="test",
            scope="global",
        )
    await fts_session.commit()

    hits = await sr.fts_search(fts_session, "pagination", limit=2, offset=0)
    assert len(hits) == 2


@pytest.mark.asyncio
async def test_fts_search_offset_skips_first_page(
    fts_session: AsyncSession,
) -> None:
    """offset=N shifts the result window by N."""
    for i in range(3):
        await kr.create(
            fts_session,
            title=f"offset test entry {i}",
            summary="pagination",
            content=f"offset pagination test item number {i}",
            tags="test",
            scope="global",
        )
    await fts_session.commit()

    page1 = await sr.fts_search(fts_session, "pagination", limit=2, offset=0)
    page2 = await sr.fts_search(fts_session, "pagination", limit=2, offset=2)
    # Second page should have different IDs than first page.
    page1_ids = {k.id for k in page1}
    page2_ids = {k.id for k in page2}
    assert len(page2) >= 1
    assert page1_ids.isdisjoint(page2_ids), (
        f"offset=1 should return different IDs, got overlap: "
        f"page1={page1_ids} page2={page2_ids}"
    )


@pytest.mark.asyncio
async def test_fts_search_offset_beyond_results_returns_empty(
    fts_session: AsyncSession,
) -> None:
    """offset >= total count returns empty list."""
    await kr.create(
        fts_session,
        title="only one",
        summary="unique",
        content="only one entry",
        tags="test",
        scope="global",
    )
    await fts_session.commit()

    hits = await sr.fts_search(fts_session, "unique", limit=2, offset=5)
    assert hits == []


# ---------------------------------------------------------------------------
# Integration — real hybrid path, requires Ollama.
# ---------------------------------------------------------------------------


def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


_OLLAMA = _ollama_available()


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


@pytest_asyncio.fixture
async def offset_service(tmp_path: Path):
    if not _OLLAMA:
        pytest.skip("Ollama not reachable — offset pagination integration skipped")
    db_path = tmp_path / "mnemo-offset.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    event.listen(engine.sync_engine, "connect", _load_sqlite_vec)
    await _init_schema(engine)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = MnemoConfig(_env_file=None)  # type: ignore[call-arg]
    embedding = EmbeddingService(config=config)
    if not await embedding.warmup():
        await engine.dispose()
        pytest.skip("Ollama warmup failed")

    service = KnowledgeService(
        session_factory=factory, config=config, embedding_service=embedding
    )
    # Seed 3 entries with similar content so they all match the same query.
    for i in range(3):
        await service.create_knowledge(
            title=f"分页测试条目 {i}",
            summary=f"pagination test item {i}",
            content=f"这是一条用于测试 offset 分页的第 {i} 条知识。",
            tags="test,offset",
            scope="global",
        )
    try:
        yield service
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_offset_pagination(
    offset_service: KnowledgeService,
) -> None:
    """search(limit=2, offset=0) and search(limit=2, offset=1) return
    disjoint result sets."""
    page1 = await offset_service.search("分页测试", limit=2, offset=0)
    assert len(page1) == 2, f"expected 2 hits page 1, got {len(page1)}"

    page2 = await offset_service.search("分页测试", limit=2, offset=2)
    assert len(page2) == 1, f"expected 1 hit page 2, got {len(page2)}"

    page1_ids = {r["id"] for r in page1}
    page2_ids = {r["id"] for r in page2}
    assert page1_ids.isdisjoint(page2_ids), (
        f"pages should not overlap: {page1_ids} & {page2_ids}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_offset_defaults_to_zero(
    offset_service: KnowledgeService,
) -> None:
    """Default offset=0 is backward compatible."""
    results = await offset_service.search("分页测试", limit=3)
    assert len(results) == 3
