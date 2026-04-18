"""Chinese full-text search tests backed by jieba pre-tokenization.

These tests would fail with the default FTS5 unicode61 tokenizer because it
treats each CJK character as its own token, so a query like "批量测试" only
matches documents containing that exact 4-char substring in order. With
jieba segmentation of both the indexed text and the query, "批量" and "测试"
become independent tokens and cross-sentence matches succeed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mnemo.models.knowledge import Base
from mnemo.repository import (
    knowledge_repository as kr,
    search_repository as sr,
)
from mnemo.services.knowledge_service import KnowledgeService
from mnemo.utils.tokenizer import tokenize_for_fts


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    db_path = tmp_path / "mnemo.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
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
    try:
        yield KnowledgeService(session_factory=factory)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# tokenizer unit tests
# ---------------------------------------------------------------------------

def test_tokenize_splits_contiguous_chinese() -> None:
    out = tokenize_for_fts("批量测试中文分词")
    segments = out.split()
    # Every segment non-empty; at least "批量" "测试" "中文" "分词" are produced
    assert all(s for s in segments)
    assert "批量" in segments
    assert "测试" in segments
    assert "中文" in segments
    assert "分词" in segments


def test_tokenize_preserves_ascii_words() -> None:
    assert tokenize_for_fts("SQLite FTS5 基础") .split().count("SQLite") == 1
    out = tokenize_for_fts("SQLite FTS5 基础")
    assert "SQLite" in out
    assert "FTS5" in out
    assert "基础" in out


def test_tokenize_empty_inputs() -> None:
    assert tokenize_for_fts("") == ""
    assert tokenize_for_fts(None) == ""
    assert tokenize_for_fts("   \t\n  ") == ""


def test_tokenize_collapses_whitespace() -> None:
    # Internal multi-space runs collapse to single spaces
    out = tokenize_for_fts("中文   测试")
    assert "  " not in out


def test_tokenize_is_deterministic() -> None:
    a = tokenize_for_fts("批量测试中文分词效果")
    b = tokenize_for_fts("批量测试中文分词效果")
    assert a == b


# ---------------------------------------------------------------------------
# repository-level search with jieba
# ---------------------------------------------------------------------------

async def test_chinese_phrase_matches_after_segmentation(
    session: AsyncSession,
) -> None:
    # Neither of "批量" nor "测试" appears as a standalone word in the source;
    # without jieba, "批量测试" as a substring would still match, so we use
    # content where the terms are separated.
    await kr.create(
        session,
        title="日志系统",
        summary="后端日志规范",
        content="我们需要一套完整的批量数据处理流程，并对每个模块做集成测试。",
    )

    hits = await sr.fts_search(session, "批量测试")
    # Both "批量" and "测试" are present (segmented at index time), query
    # segments into the same tokens, so the document matches with implicit AND.
    assert len(hits) == 1
    assert hits[0].title == "日志系统"


async def test_chinese_tokenization_topic_match(session: AsyncSession) -> None:
    await kr.create(
        session,
        title="NLP 基础",
        summary="自然语言处理简介",
        content="中文分词是 NLP 的基础任务之一。",
    )

    hits = await sr.fts_search(session, "中文分词")
    assert len(hits) == 1
    assert hits[0].title == "NLP 基础"


async def test_chinese_query_does_not_match_unrelated_doc(
    session: AsyncSession,
) -> None:
    await kr.create(
        session, title="无关", summary="s", content="这个文档只讲前端。"
    )
    await kr.create(
        session,
        title="分词引擎",
        summary="介绍 jieba",
        content="jieba 负责中文分词。",
    )

    hits = await sr.fts_search(session, "中文分词")
    # Only the 分词引擎 doc actually contains both "中文" and "分词"
    assert [h.title for h in hits] == ["分词引擎"]


async def test_chinese_query_intersects_with_and_semantics(
    session: AsyncSession,
) -> None:
    # Doc A has "批量" only, Doc B has "测试" only, Doc C has both
    await kr.create(session, title="A", summary="s", content="批量导入数据。")
    await kr.create(session, title="B", summary="s", content="集成测试覆盖率。")
    await kr.create(
        session,
        title="C",
        summary="s",
        content="批量运行测试以验证稳定性。",
    )

    hits = await sr.fts_search(session, "批量测试")
    assert {h.title for h in hits} == {"C"}


async def test_mixed_zh_en_query(session: AsyncSession) -> None:
    await kr.create(
        session,
        title="混合",
        summary="s",
        content="SQLite 搭配 jieba 做中文搜索。",
    )

    hits = await sr.fts_search(session, "SQLite 中文")
    assert len(hits) == 1
    assert hits[0].title == "混合"


async def test_chinese_tag_is_searchable(session: AsyncSession) -> None:
    await kr.create(
        session,
        title="有标签",
        summary="s",
        content="英文正文。",
        tags=["中文处理", "向量检索"],
    )

    hits = await sr.fts_search(session, "中文处理")
    assert len(hits) == 1
    assert hits[0].title == "有标签"


async def test_chinese_title_hit(session: AsyncSession) -> None:
    await kr.create(
        session,
        title="向量数据库调研",
        summary="各家对比",
        content="content body here",
    )

    hits = await sr.fts_search(session, "向量数据库")
    assert {h.title for h in hits} == {"向量数据库调研"}


async def test_chinese_update_reindexes_with_jieba(session: AsyncSession) -> None:
    row = await kr.create(
        session,
        title="占位",
        summary="s",
        content="占位正文，不含关键词。",
    )

    assert await sr.fts_search(session, "知识图谱") == []

    await kr.update(
        session,
        row.id,
        content="本文介绍知识图谱的构建流程。",
    )

    hits = await sr.fts_search(session, "知识图谱")
    assert [h.id for h in hits] == [row.id]


# ---------------------------------------------------------------------------
# English search still works (backwards compatibility)
# ---------------------------------------------------------------------------

async def test_english_search_unaffected(session: AsyncSession) -> None:
    await kr.create(
        session, title="A", summary="s", content="apple banana cherry"
    )
    await kr.create(
        session, title="B", summary="s", content="durian pomelo"
    )

    apple_hits = await sr.fts_search(session, "apple")
    assert [h.title for h in apple_hits] == ["A"]

    pomelo_hits = await sr.fts_search(session, "pomelo")
    assert [h.title for h in pomelo_hits] == ["B"]

    assert await sr.fts_search(session, "nonexistent") == []


async def test_english_sanitize_still_handles_punctuation(
    session: AsyncSession,
) -> None:
    await kr.create(session, title="T", summary="s", content="hello world")
    # Punctuation must not blow up FTS; should still match
    hits = await sr.fts_search(session, 'hello, "world"!')
    assert len(hits) == 1


async def test_empty_query_returns_empty(session: AsyncSession) -> None:
    await kr.create(session, title="T", summary="s", content="中文内容")
    assert await sr.fts_search(session, "") == []
    assert await sr.fts_search(session, "   ") == []


# ---------------------------------------------------------------------------
# service layer: end-to-end Chinese search
# ---------------------------------------------------------------------------

async def test_service_search_chinese(service: KnowledgeService) -> None:
    await service.create_knowledge(
        title="FTS5 中文检索",
        summary="搜索能力",
        content="使用 jieba 对中文做分词后入索引。",
        tags=["中文", "检索"],
    )
    await service.create_knowledge(
        title="向量库",
        summary="embedding",
        content="向量检索是另一种思路。",
    )

    hits = await service.search("中文分词")
    assert len(hits) == 1
    assert hits[0]["title"] == "FTS5 中文检索"

    vec_hits = await service.search("向量检索")
    titles = {h["title"] for h in vec_hits}
    # Both docs mention 向量 or 检索 — 向量库 contains both tokens, FTS5 中文检索
    # contains 检索 but not 向量. With AND semantics, only 向量库 matches.
    assert titles == {"向量库"}
