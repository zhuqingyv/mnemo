"""Hybrid search integration tests — real Ollama + full 733-entry fixture.

Builds an independent session-scoped fixture tree (separate from
``scenario_conftest``) so the hybrid path gets its own sqlite-vec-enabled
engine and a live ``EmbeddingService`` wired into ``KnowledgeService``.

Marked ``integration`` — skip with ``-m "not integration"`` when Ollama is
absent. We probe Ollama once at module import and skip the whole file if
the service isn't reachable.
"""

from __future__ import annotations

import json
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
from mnemo.repository import knowledge_repository as kr
from mnemo.services.embedding_service import EmbeddingService
from mnemo.services.knowledge_service import (
    MANUAL_RELATION_TYPE,
    WIKILINK_RELATION_TYPE,
    KnowledgeService,
)


pytestmark = pytest.mark.integration


REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "tests" / "fixtures" / "knowledge"


def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


if not _ollama_available():
    pytest.skip(
        "Ollama not reachable on localhost:11434 — hybrid integration tests skipped",
        allow_module_level=True,
    )


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


def _load_knowledge_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    entries.append(item)
    return entries


async def _insert_all(service: KnowledgeService, entries: list[dict[str, Any]]) -> int:
    inserted = 0
    for item in entries:
        title = item.get("title")
        if not title:
            continue
        try:
            await service.create_knowledge(
                title=title,
                summary=item.get("summary") or "",
                content=item.get("content") or "",
                tags=item.get("tags") or None,
                scope=item.get("scope") or "global",
                project_name=item.get("project_name"),
                source=item.get("source"),
                claim_type=item.get("claim_type"),
                related_titles=item.get("related") or None,
            )
            inserted += 1
        except Exception:
            continue
    return inserted


async def _reapply_relations(
    service: KnowledgeService, entries: list[dict[str, Any]]
) -> None:
    factory = service._session_factory  # noqa: SLF001
    async with factory() as session:
        for item in entries:
            title = item.get("title")
            if not title:
                continue
            row = await kr.get_by_title(session, title)
            if row is None:
                continue
            await session.execute(
                text(
                    "DELETE FROM relation "
                    "WHERE source_id = :sid AND relation_type IN (:t1, :t2)"
                ),
                {
                    "sid": row.id,
                    "t1": WIKILINK_RELATION_TYPE,
                    "t2": MANUAL_RELATION_TYPE,
                },
            )
            await session.commit()
            await service._apply_wikilinks(session, row.id, row.content)  # noqa: SLF001
            await service._apply_manual_relations(  # noqa: SLF001
                session, row.id, item.get("related") or None
            )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def hybrid_service(tmp_path_factory):
    """KnowledgeService with EmbeddingService wired + sqlite-vec loaded."""
    tmp_dir = tmp_path_factory.mktemp("mnemo-hybrid")
    db_path = tmp_dir / "mnemo.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", echo=False
    )
    event.listen(engine.sync_engine, "connect", _load_sqlite_vec)
    await _init_schema(engine)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    config = MnemoConfig()
    embedding = EmbeddingService(config=config)
    ok = await embedding.warmup()
    if not ok:
        await engine.dispose()
        pytest.skip("Ollama warmup failed — hybrid integration tests skipped")

    service = KnowledgeService(
        session_factory=session_factory,
        config=config,
        embedding_service=embedding,
    )

    entries = _load_knowledge_entries()
    inserted = await _insert_all(service, entries)
    await _reapply_relations(service, entries)

    print(f"\n[hybrid_service] loaded {inserted}/{len(entries)} entries")

    try:
        yield service
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Integration cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_search_hybrid_returns_results(hybrid_service):
    """Default mode should be hybrid and return non-empty for a common query."""
    results = await hybrid_service.search("单测", limit=10)
    assert len(results) > 0, "hybrid search returned empty for '单测'"
    for r in results:
        assert "id" in r
        assert "title" in r
        assert r.get("source") in ("both", "fts_only", "vec_only")


@pytest.mark.asyncio(loop_scope="session")
async def test_search_fts_mode_unchanged(hybrid_service):
    """mode='fts' keeps Phase 1 behavior — no rrf_score / source fields."""
    results = await hybrid_service.search("单测", limit=10, mode="fts")
    assert len(results) > 0
    for r in results:
        assert "rrf_score" not in r
        assert "source" not in r or r["source"] not in (
            "both",
            "fts_only",
            "vec_only",
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_search_vector_mode_unchanged(hybrid_service):
    """mode='vector' pure KNN path — no rrf_score / fts_rank."""
    results = await hybrid_service.search("单测", limit=10, mode="vector")
    assert len(results) > 0, "vector search returned empty — Ollama or index broken"
    for r in results:
        assert "rrf_score" not in r
        assert "fts_rank" not in r


@pytest.mark.asyncio(loop_scope="session")
async def test_search_hybrid_source_tagging(hybrid_service):
    """Hybrid results must carry source + ranks populated per channel.

    Vector channel runs full KNN independently of FTS, so all three source
    types (both / fts_only / vec_only) are possible.
    """
    results = await hybrid_service.search("单元测试", limit=20)
    assert len(results) > 0
    sources = {r["source"] for r in results}
    assert sources.issubset({"both", "fts_only", "vec_only"}), (
        f"unexpected source values: {sources}"
    )
    assert "both" in sources, (
        "expected at least one 'both' result — FTS + cosine both should fire"
    )
    for r in results:
        if r["source"] == "both":
            assert r["fts_rank"] is not None and r["vec_rank"] is not None
        elif r["source"] == "fts_only":
            assert r["fts_rank"] is not None and r["vec_rank"] is None
        else:  # vec_only shouldn't appear in FTS-hit path
            assert r["fts_rank"] is None and r["vec_rank"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_search_hybrid_respects_scope(hybrid_service):
    """Scope filter must still work under hybrid."""
    project_results = await hybrid_service.search(
        "架构", scope="project", limit=20
    )
    assert len(project_results) > 0
    assert all(r["scope"] == "project" for r in project_results)


@pytest.mark.asyncio(loop_scope="session")
async def test_search_hybrid_beats_or_matches_fts_on_colloquial(
    hybrid_service, capsys
):
    """Colloquial INT-style queries: hybrid Top-3 hit rate ≥ pure FTS."""
    cases = [
        ("用户脾气", ["直接简洁不要 AI 腔", "说话带证据不要含糊"]),
        ("不要废话", ["直接简洁不要 AI 腔", "回答完不要再总结"]),
        ("怎么测试", ["不 mock 测试", "新模块必须带单测"]),
        ("中文搜不到怎么办", ["中文搜索先用英文关键词兜底", "FTS5 中文分词不完善"]),
        ("做完怎么算交付", ["有证据才能说完成", "交付前必过 delivery-gate"]),
    ]

    fts_hits = 0
    hybrid_hits = 0
    detail: list[tuple[str, bool, bool]] = []
    for query, expected in cases:
        fts_rs = await hybrid_service.search(query, limit=3, mode="fts")
        hy_rs = await hybrid_service.search(query, limit=3, mode="hybrid")
        fts_titles = [r["title"] for r in fts_rs]
        hy_titles = [r["title"] for r in hy_rs]
        f = any(t in fts_titles for t in expected)
        h = any(t in hy_titles for t in expected)
        fts_hits += int(f)
        hybrid_hits += int(h)
        detail.append((query, f, h))

    with capsys.disabled():
        print("\n===== Hybrid vs FTS (colloquial) =====")
        print(f"{'query':<20} {'FTS':>5} {'HYB':>5}")
        for q, f, h in detail:
            print(f"{q:<20} {'PASS' if f else 'FAIL':>5} {'PASS' if h else 'FAIL':>5}")
        print(f"Total: FTS={fts_hits}/{len(cases)}  HYBRID={hybrid_hits}/{len(cases)}")

    assert hybrid_hits >= fts_hits, (
        f"hybrid Top-3 ({hybrid_hits}) regressed vs FTS ({fts_hits}) on "
        f"colloquial queries: {detail}"
    )
