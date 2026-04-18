"""Integration tests for the MCP server tools.

Each test drives the FastMCP instance via ``mcp.call_tool`` against a real
KnowledgeService wired to a fresh file-based SQLite DB. No mocks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.mcp import server as mcp_server
from mnemo.models.knowledge import Base
from mnemo.services.knowledge_service import KnowledgeService


def _text(result) -> str:
    """FastMCP wraps a string return in ToolResult.content[0].text."""
    return result.content[0].text


@pytest_asyncio.fixture
async def mcp_env(tmp_path: Path) -> AsyncIterator[KnowledgeService]:
    """Install a fresh KnowledgeService into the module-level MCP server.

    Restores whatever was previously registered so parallel tests don't leak.
    """
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
    service = KnowledgeService(session_factory=factory)

    previous = mcp_server._service
    mcp_server.set_service(service)
    try:
        yield service
    finally:
        mcp_server._service = previous
        await engine.dispose()


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def test_split_csv_handles_empty_and_spaces() -> None:
    assert mcp_server._split_csv("") == []
    assert mcp_server._split_csv(None) == []
    assert mcp_server._split_csv("a, b ,c") == ["a", "b", "c"]
    assert mcp_server._split_csv(" , , ") == []


def test_parse_id_or_title_distinguishes_digits_from_titles() -> None:
    assert mcp_server._parse_id_or_title("42") == 42
    assert mcp_server._parse_id_or_title("Some Title") == "Some Title"
    # leading/trailing space tolerated
    assert mcp_server._parse_id_or_title("  7 ") == 7


def test_sanitize_claim_type_accepts_whitelist() -> None:
    for good in ("fact", "decision", "procedure", "hypothesis"):
        assert mcp_server._sanitize_claim_type(good) == good
    # whitespace around a whitelisted value still passes
    assert mcp_server._sanitize_claim_type("  fact  ") == "fact"


def test_sanitize_claim_type_rejects_xml_shard_and_unknown() -> None:
    # None / empty → None
    assert mcp_server._sanitize_claim_type(None) is None
    assert mcp_server._sanitize_claim_type("") is None
    assert mcp_server._sanitize_claim_type("   ") is None
    # unknown strings and XML-shaped shards drop to None (logged)
    assert mcp_server._sanitize_claim_type("mystery") is None
    xml_shard = 'decision</claim_type>\n<parameter name="scope">project'
    assert mcp_server._sanitize_claim_type(xml_shard) is None


# ---------------------------------------------------------------------------
# create_knowledge
# ---------------------------------------------------------------------------

async def test_create_tool_returns_markdown_with_id_and_tags(
    mcp_env: KnowledgeService,
) -> None:
    result = await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "SQLite FTS5 中文分词方案",
            "tags": "sqlite, 中文, 搜索",
            "summary": "SQLite FTS5 默认不支持中文分词",
            "content": "use the ICU tokenizer",
        },
    )
    body = _text(result)
    assert "SQLite FTS5 中文分词方案" in body
    assert "sqlite, 中文, 搜索" in body
    assert "id:" in body


async def test_create_tool_respects_related(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(title="Python", summary="s", content="c")
    await mcp_env.create_knowledge(title="Rust", summary="s", content="c")

    result = await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "Comparison",
            "tags": "",
            "summary": "compare",
            "content": "no wikilinks",
            "related": "Python, Rust, Missing",
        },
    )
    body = _text(result)
    assert "Python" in body and "Rust" in body
    assert "Missing" not in body


async def test_create_tool_picks_up_wikilinks(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(title="Target", summary="s", content="c")
    result = await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "Host",
            "tags": "",
            "summary": "s",
            "content": "see [[Target]]",
        },
    )
    assert "Target" in _text(result)


async def test_create_tool_renders_write_gate_with_recommended_action(
    mcp_env: KnowledgeService,
) -> None:
    # Short content triggers evidence_weak=content_too_short (default min 50).
    result = await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "GateCheck",
            "tags": "",
            "summary": "s",
            "content": "short",
        },
    )
    body = _text(result)
    assert "Write-gate report" in body
    assert "Recommended action" in body
    assert "Evidence weak" in body
    assert "content_too_short" in body


async def test_create_tool_rejects_invalid_claim_type_end_to_end(
    mcp_env: KnowledgeService,
) -> None:
    """The MCP layer must scrub XML-shard / unknown claim_type values so they
    never reach storage. Regression guard for the ID=89 contamination bug
    (agent pasted a tool-call XML fragment into the claim_type slot)."""
    xml_shard = 'decision</claim_type>\n<parameter name="scope">project'
    await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "claim-type-sanity",
            "tags": "",
            "summary": "s",
            "content": "body text long enough to pass the write gate. " * 3,
            "claim_type": xml_shard,
        },
    )
    # legal values still pass through
    await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "claim-type-legal",
            "tags": "",
            "summary": "s",
            "content": "body text long enough to pass the write gate. " * 3,
            "claim_type": "decision",
        },
    )
    dirty = await mcp_env.get_knowledge("claim-type-sanity")
    legal = await mcp_env.get_knowledge("claim-type-legal")
    assert dirty is not None and legal is not None
    assert dirty["claim_type"] is None, (
        f"XML shard should be scrubbed to None, got {dirty['claim_type']!r}"
    )
    assert legal["claim_type"] == "decision"


async def test_create_tool_write_gate_flags_title_similar(
    mcp_env: KnowledgeService,
) -> None:
    # Long enough content to clear evidence_weak; second create triggers L1/L2.
    long_content = "Detailed notes about asyncio usage patterns in real-world code. " * 3
    await mcp_env.create_knowledge(
        title="Python async guide", summary="intro", content=long_content
    )
    result = await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "Python async guide v2",
            "tags": "",
            "summary": "intro",
            "content": long_content + " revised",
        },
    )
    body = _text(result)
    assert "Write-gate report" in body
    # near-duplicate title should surface as L1 title_similar or L2 semantic_similar
    assert ("Title similar" in body) or ("Semantic similar" in body)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

async def test_search_tool_formats_results(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(
        title="pineapple", summary="a fruit", content="yellow"
    )
    await mcp_env.create_knowledge(
        title="banana", summary="another", content="yellow fruit body"
    )
    result = await mcp_server.mcp.call_tool("search", {"query": "yellow"})
    body = _text(result)
    assert 'Search Results for "yellow"' in body
    assert "pineapple" in body
    assert "banana" in body


async def test_search_tool_no_hits(mcp_env: KnowledgeService) -> None:
    result = await mcp_server.mcp.call_tool(
        "search", {"query": "definitely-missing-token"}
    )
    assert "No results" in _text(result)


async def test_search_tool_surfaces_claim_type_status_version(
    mcp_env: KnowledgeService,
) -> None:
    await mcp_env.create_knowledge(
        title="decision-note",
        summary="s",
        content="marker-token body",
        claim_type="decision",
    )
    result = await mcp_server.mcp.call_tool("search", {"query": "marker-token"})
    body = _text(result)
    assert "**Type:** decision" in body
    assert "**Status:** active" in body
    assert "v1" in body


async def test_search_tool_surfaces_conflicts_with() -> None:
    # exercise the formatter directly — the full-stack flag gating is covered
    # elsewhere; here we only care that the markdown surfaces the field.
    item = {
        "id": 42,
        "title": "beta",
        "tags": [],
        "summary": "s",
        "scope": "global",
        "project_name": None,
        "claim_type": "fact",
        "status": "active",
        "version": 1,
        "conflicts_with": [17, 99],
    }
    rendered = mcp_server._format_summary_entry(1, item)
    assert "**Conflicts with:**" in rendered
    assert "#17" in rendered and "#99" in rendered


async def test_search_tool_scope_filter(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(
        title="G", summary="s", content="uniqueword", scope="global"
    )
    await mcp_env.create_knowledge(
        title="P",
        summary="s",
        content="uniqueword",
        scope="project",
        project_name="mnemo",
    )
    result = await mcp_server.mcp.call_tool(
        "search", {"query": "uniqueword", "scope": "project"}
    )
    body = _text(result)
    assert "P" in body and "G" not in body.replace("global", "")


# ---------------------------------------------------------------------------
# get_knowledge
# ---------------------------------------------------------------------------

async def test_get_tool_by_title_and_id(mcp_env: KnowledgeService) -> None:
    created = await mcp_env.create_knowledge(
        title="K1", tags=["x"], summary="sum", content="body"
    )

    by_title = await mcp_server.mcp.call_tool(
        "get_knowledge", {"id_or_title": "K1"}
    )
    by_id = await mcp_server.mcp.call_tool(
        "get_knowledge", {"id_or_title": str(created["id"])}
    )
    for result in (by_title, by_id):
        body = _text(result)
        assert "K1" in body
        assert "body" in body  # content rendered


async def test_get_tool_missing(mcp_env: KnowledgeService) -> None:
    result = await mcp_server.mcp.call_tool(
        "get_knowledge", {"id_or_title": "nope"}
    )
    assert "not found" in _text(result).lower()


# ---------------------------------------------------------------------------
# update_knowledge
# ---------------------------------------------------------------------------

async def test_update_tool_changes_fields(mcp_env: KnowledgeService) -> None:
    created = await mcp_env.create_knowledge(
        title="Old", summary="s", content="c", tags=["a"]
    )
    result = await mcp_server.mcp.call_tool(
        "update_knowledge",
        {"id": created["id"], "title": "New", "tags": "b,c"},
    )
    body = _text(result)
    assert "New" in body
    assert "b, c" in body


async def test_update_tool_no_fields(mcp_env: KnowledgeService) -> None:
    created = await mcp_env.create_knowledge(
        title="Only", summary="s", content="c"
    )
    result = await mcp_server.mcp.call_tool(
        "update_knowledge", {"id": created["id"]}
    )
    assert "No fields" in _text(result)


# ---------------------------------------------------------------------------
# delete_knowledge
# ---------------------------------------------------------------------------

async def test_delete_tool_success_and_missing(
    mcp_env: KnowledgeService,
) -> None:
    created = await mcp_env.create_knowledge(
        title="Doomed", summary="s", content="c"
    )
    ok = await mcp_server.mcp.call_tool(
        "delete_knowledge", {"id": created["id"]}
    )
    assert "Deleted" in _text(ok)

    again = await mcp_server.mcp.call_tool(
        "delete_knowledge", {"id": created["id"]}
    )
    assert "No knowledge" in _text(again)


# ---------------------------------------------------------------------------
# get_related
# ---------------------------------------------------------------------------

async def test_related_tool_shows_neighbors(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(title="A", summary="s", content="c")
    await mcp_env.create_knowledge(
        title="B", summary="s", content="links [[A]]"
    )
    result = await mcp_server.mcp.call_tool(
        "get_related", {"id_or_title": "A"}
    )
    assert "B" in _text(result)


async def test_related_tool_empty(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(title="Lonely", summary="s", content="c")
    result = await mcp_server.mcp.call_tool(
        "get_related", {"id_or_title": "Lonely"}
    )
    assert "No related" in _text(result)


# ---------------------------------------------------------------------------
# list_tags / search_by_tag
# ---------------------------------------------------------------------------

async def test_list_tags_tool(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(
        title="k1", summary="s", content="c", tags=["python", "db"]
    )
    await mcp_env.create_knowledge(
        title="k2",
        summary="s",
        content="c",
        tags=["rust"],
        scope="project",
        project_name="mnemo",
    )

    all_tags = await mcp_server.mcp.call_tool("list_tags", {})
    body = _text(all_tags)
    assert "python" in body and "db" in body and "rust" in body

    global_only = await mcp_server.mcp.call_tool(
        "list_tags", {"scope": "global"}
    )
    body = _text(global_only)
    assert "python" in body and "rust" not in body


async def test_list_tags_empty(mcp_env: KnowledgeService) -> None:
    result = await mcp_server.mcp.call_tool("list_tags", {})
    assert "No tags" in _text(result)


async def test_search_by_tag_tool(mcp_env: KnowledgeService) -> None:
    await mcp_env.create_knowledge(
        title="a", summary="s", content="c", tags=["t1", "t2"]
    )
    await mcp_env.create_knowledge(
        title="b", summary="s", content="c", tags=["t1"]
    )

    single = await mcp_server.mcp.call_tool(
        "search_by_tag", {"tags": "t1"}
    )
    body = _text(single)
    assert "a" in body and "b" in body

    both = await mcp_server.mcp.call_tool(
        "search_by_tag", {"tags": "t1,t2"}
    )
    body = _text(both)
    assert "a" in body and "\nb\n" not in body  # b lacks t2


async def test_search_by_tag_requires_tags(mcp_env: KnowledgeService) -> None:
    result = await mcp_server.mcp.call_tool("search_by_tag", {"tags": ""})
    assert "No tags" in _text(result)


async def test_search_by_tag_no_hits(mcp_env: KnowledgeService) -> None:
    result = await mcp_server.mcp.call_tool(
        "search_by_tag", {"tags": "nosuchtag"}
    )
    assert "No knowledge" in _text(result)


# ---------------------------------------------------------------------------
# registration sanity
# ---------------------------------------------------------------------------

async def test_all_eight_tools_are_registered() -> None:
    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    assert {
        "create_knowledge",
        "search",
        "get_knowledge",
        "update_knowledge",
        "delete_knowledge",
        "get_related",
        "list_tags",
        "search_by_tag",
    } <= names


async def test_service_guard_raises_when_not_injected() -> None:
    previous = mcp_server._service
    mcp_server._service = None
    try:
        with pytest.raises(RuntimeError):
            mcp_server._require_service()
    finally:
        mcp_server._service = previous


# ---------------------------------------------------------------------------
# monitor collection end-to-end — Bug 1 (actor) + Bug 2 (result_meta)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def monitored_mcp_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker]:
    """Like mcp_env but also wires the monitor collector to the same DB so we
    can assert the events that land after each tool call."""
    import asyncio as _asyncio
    from mnemo.monitor import collector as _collector
    from mnemo.monitor.models import MonitorEvent  # noqa: F401 — imported for Base

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
    service = KnowledgeService(session_factory=factory)
    previous_service = mcp_server._service
    mcp_server.set_service(service)
    _collector.configure(session_factory=factory, enabled=True)
    monkeypatch.setenv("MNEMO_ACTOR", "agent:test-runner")
    try:
        yield factory
    finally:
        mcp_server._service = previous_service
        _collector.configure(session_factory=None, enabled=True)
        await engine.dispose()
        # Let any straggling writer tasks finish before teardown.
        for _ in range(5):
            await _asyncio.sleep(0)


async def _drain_events(
    factory: async_sessionmaker, min_count: int, budget: int = 50
) -> list:
    """Poll for at least ``min_count`` monitor_event rows — SQLite async
    commits need more yields than the background-task scheduler guarantees."""
    import asyncio as _asyncio
    from sqlalchemy import select as _select
    from mnemo.monitor.models import MonitorEvent as _ME

    for _ in range(budget):
        await _asyncio.sleep(0.01)
        async with factory() as s:
            r = await s.execute(_select(_ME).order_by(_ME.id))
            events = list(r.scalars().all())
        if len(events) >= min_count:
            return events
    return events


async def test_search_tool_populates_actor_and_result_meta(
    monitored_mcp_env: async_sessionmaker,
) -> None:
    """Bug 1 + Bug 2 regression: after a real MCP search call the event row
    must carry a non-unknown actor and a non-null result_meta with business
    dimensions (hits / mode / sort_by)."""
    import json as _json

    await mcp_server.mcp.call_tool("search", {"query": "never-matches-xyz"})
    events = await _drain_events(monitored_mcp_env, min_count=1)
    assert len(events) == 1
    ev = events[0]
    assert ev.tool_name == "search"
    assert ev.actor == "agent:test-runner"  # MNEMO_ACTOR fallback populates
    assert ev.actor != "agent:unknown"
    assert ev.session_id is not None  # proc fallback, not NULL
    assert ev.result_meta is not None
    meta = _json.loads(ev.result_meta)
    assert meta["hits"] == 0
    assert meta["mode"] == "hybrid"
    assert meta["sort_by"] == "relevance"


async def test_create_tool_records_knowledge_id_and_action(
    monitored_mcp_env: async_sessionmaker,
) -> None:
    import json as _json

    await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "monitor-observability demo",
            "tags": "monitor,observability",
            "summary": "demo summary used only in monitor tests",
            "content": "demo content used only in monitor tests",
        },
    )
    events = await _drain_events(monitored_mcp_env, min_count=1)
    assert events, "no events written"
    ev = next(e for e in events if e.tool_name == "create_knowledge")
    assert ev.actor == "agent:test-runner"
    assert ev.knowledge_id is not None and ev.knowledge_id > 0
    assert ev.result_meta is not None
    meta = _json.loads(ev.result_meta)
    # create_knowledge lifts knowledge_id/scope to columns; leftover keys
    # (recommended_action, duplicate_warning, ...) land in result_meta.
    assert "recommended_action" in meta
    assert meta["duplicate_warning"] is False


async def test_feedback_tool_persists_caller_actor(
    monitored_mcp_env: async_sessionmaker,
) -> None:
    """Bug 1 specific: feedback_knowledge receives an explicit actor; that
    actor must win over the MNEMO_ACTOR env fallback in the monitor row."""
    # Seed one knowledge row so feedback has a valid target.
    result = await mcp_server.mcp.call_tool(
        "create_knowledge",
        {
            "title": "feedback-target",
            "tags": "t",
            "summary": "s",
            "content": "c",
        },
    )
    _ = result  # body not asserted here

    # Extract the id from the existing knowledge row via service directly.
    async with monitored_mcp_env() as s:
        from sqlalchemy import select as _select
        from mnemo.models.knowledge import Knowledge as _K
        r = await s.execute(_select(_K.id).where(_K.title == "feedback-target"))
        kid = r.scalar_one()

    await mcp_server.mcp.call_tool(
        "feedback_knowledge",
        {
            "knowledge_id": kid,
            "signal": "helpful",
            "actor": "agent:explicit-caller",
        },
    )

    events = await _drain_events(monitored_mcp_env, min_count=2)
    fb_events = [e for e in events if e.tool_name == "feedback_knowledge"]
    assert fb_events, "expected a feedback_knowledge monitor event"
    fb = fb_events[-1]
    assert fb.actor == "agent:explicit-caller"
    assert fb.knowledge_id == kid
    assert fb.result_meta is not None
