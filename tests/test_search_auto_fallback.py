"""Tests for the P0 UX auto-fallback (Task A).

Two layers:
1. Pure unit tests for ``_shorten_query_for_fallback`` — no DB, no Ollama.
2. Integration test gated on Ollama reachability that exercises the real
   ``KnowledgeService.search()`` hybrid path end-to-end with the flag on/off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import requests
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import sqlite_vec

from mnemo.config import MnemoConfig
from mnemo.db import VECTOR_DIM
from mnemo.models.knowledge import Base
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services.knowledge_service import (
    KnowledgeService,
    _shorten_query_for_fallback,
)


# ---------------------------------------------------------------------------
# Pure helper — runs without Ollama.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        # Empty / whitespace.
        ("", None),
        ("   ", None),
        # All-stopword inputs have nothing material to keep.
        ("怎么", None),
        ("the a is", None),
        # Single distinct token can't be shortened further (shortened ==
        # original lowercased → return None to skip pointless retry).
        ("kubernetes", None),
        # Mixed CN/EN with clear stopwords stripped.
        (
            "how to use kubernetes 集群 怎么 部署",
            "kubernetes 集群 部署",
        ),
        # Stopwords and modifier 如何 dropped.
        ("如何 配置 freshness 衰减", "配置 freshness 衰减"),
        # Keeps 2-3 shortest content tokens.
        ("what is the best way to handle errors", "best way handle"),
    ],
)
def test_shorten_query_for_fallback(query: str, expected: str | None) -> None:
    assert _shorten_query_for_fallback(query) == expected


def test_shorten_preserves_order_of_kept_tokens() -> None:
    """Kept tokens retain their original relative order (not sorted by length)."""
    out = _shorten_query_for_fallback("配置 freshness 衰减 如何")
    assert out == "配置 freshness 衰减"


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
async def fallback_service(tmp_path: Path):
    if not _OLLAMA:
        pytest.skip("Ollama not reachable — auto-fallback integration skipped")
    db_path = tmp_path / "mnemo-fallback.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", echo=False
    )
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
    # Seed one distinctive record whose title/content only contain the word
    # "kubernetes" — queries containing stopwords around "kubernetes" must
    # match after auto-fallback strips the fillers.
    await service.create_knowledge(
        title="kubernetes 集群部署笔记",
        summary="k8s 集群搭建",
        content="记录一次 kubernetes 集群的部署过程与踩坑。",
        tags="kubernetes,k8s,ops",
        scope="global",
    )
    try:
        yield service
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_fallback_retries_with_shortened_query(
    fallback_service: KnowledgeService,
) -> None:
    """Hybrid search with stopword-heavy query hits fallback and tags results."""
    query = "how do I use kubernetes 怎么 部署"
    results = await fallback_service.search(query, limit=10)
    # Seeded entry title is "kubernetes 集群部署笔记" — expect a hit.
    assert results, f"expected fallback hits for {query!r}, got empty"
    # At least one result must carry the auto_fallback marker.
    assert any(r.get("auto_fallback") is True for r in results), (
        "fallback hits must be tagged with auto_fallback=True"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auto_fallback_disabled_by_flag(
    fallback_service: KnowledgeService,
) -> None:
    """When flag is off, no retry happens — zero-hit query stays zero-hit."""
    fallback_service._config.search_auto_fallback_enabled = False  # noqa: SLF001
    # Use a query that only matches after stopword stripping.
    query = "xyz-never-matches-anything 怎么 搞"
    results = await fallback_service.search(query, limit=10)
    # Either empty or no auto_fallback tag — retry must not have happened.
    assert all(not r.get("auto_fallback") for r in results)
