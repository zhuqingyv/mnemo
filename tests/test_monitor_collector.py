"""Unit tests for the monitor collection layer (task #51).

Covers:
- @monitor_tool decorator captures tool_name / params_json / result_summary
  / latency_ms on a successful async call
- record_payload() inside a tool lifts actor / session_id / knowledge_id /
  status into the MonitorEvent columns and routes extra keys to result_meta
- result_summary is truncated to RESULT_SUMMARY_MAX_CHARS
- a tool that raises still records status='error' + error_type and the
  exception propagates to the caller
- the write is truly async — the decorator returns before the DB row is
  visible, then the row appears once the event loop drains
- configure(enabled=False) makes the decorator a passthrough (no rows
  written)
- a broken session_factory triggers logger.warning, the tool still returns
  its real value, and no exception escapes

Project red lines: no mocks. Every test spins up a real aiosqlite engine
with the real schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
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

from mnemo.models.knowledge import Base
from mnemo.monitor import collector
from mnemo.monitor.collector import configure, monitor_tool, record_payload
from mnemo.monitor.models import RESULT_SUMMARY_MAX_CHARS, MonitorEvent


@pytest_asyncio.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "monitor.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys = ON"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def wired_collector(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    configure(session_factory=session_factory, enabled=True)
    try:
        yield session_factory
    finally:
        configure(session_factory=None, enabled=True)


async def _drain_tasks() -> None:
    """Give the scheduled background writer a chance to run.

    asyncio.create_task returns immediately; the row only lands after the
    loop yields back to the writer. Two ``sleep(0)`` ticks is enough for
    ``async with session_factory(): session.add(...); await commit()``.
    """
    for _ in range(5):
        await asyncio.sleep(0)


async def _fetch_events(
    factory: async_sessionmaker[AsyncSession],
) -> list[MonitorEvent]:
    async with factory() as session:
        result = await session.execute(
            select(MonitorEvent).order_by(MonitorEvent.id)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# core happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decorator_captures_basic_fields(wired_collector):
    @monitor_tool(name="create_knowledge")
    async def fake_tool(title: str, tags: str = "") -> str:
        return f"created {title!r} with {tags}"

    out = await fake_tool("hello", tags="a,b")
    assert out == "created 'hello' with a,b"

    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    ev = events[0]
    assert ev.tool_name == "create_knowledge"
    assert ev.status == "ok"
    assert ev.error_type is None
    assert ev.latency_ms >= 0
    assert ev.actor == "agent:unknown"
    # Without a FastMCP Context we expect the proc-level fallback session id
    # (MONITOR_DESIGN §3.1). Empty / None is a regression — Part B rules key on
    # session_id and would silently stop firing.
    assert ev.session_id is not None
    assert ev.session_id.startswith("proc:")

    # params_json rendered as a JSON dict keyed by parameter name.
    params = json.loads(ev.params_json)
    assert params == {"title": "hello", "tags": "a,b"}

    assert "created 'hello' with a,b" in ev.result_summary


@pytest.mark.asyncio
async def test_record_payload_lifts_columns_and_merges_meta(wired_collector):
    @monitor_tool()
    async def tool_with_payload(x: int) -> dict:
        record_payload(
            actor="agent:claude",
            session_id="sess-42",
            knowledge_id=7,
            hits=3,
            top1_score=0.91,
        )
        return {"x": x, "ok": True}

    await tool_with_payload(5)
    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    ev = events[0]
    assert ev.actor == "agent:claude"
    assert ev.session_id == "sess-42"
    assert ev.knowledge_id == 7
    assert ev.result_meta is not None
    meta = json.loads(ev.result_meta)
    assert meta == {"hits": 3, "top1_score": 0.91}


@pytest.mark.asyncio
async def test_result_summary_truncated(wired_collector):
    big = "x" * (RESULT_SUMMARY_MAX_CHARS + 200)

    @monitor_tool()
    async def big_tool() -> str:
        return big

    await big_tool()
    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    summary = events[0].result_summary
    assert len(summary) == RESULT_SUMMARY_MAX_CHARS
    assert summary.endswith("...")


# ---------------------------------------------------------------------------
# error propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exception_propagates_and_records_error(wired_collector):
    @monitor_tool(name="boom")
    async def failing_tool() -> str:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        await failing_tool()

    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    ev = events[0]
    assert ev.tool_name == "boom"
    assert ev.status == "error"
    assert ev.error_type == "ValueError"
    # Error branch now captures the exception message.
    assert ev.result_summary == "kaboom"


# ---------------------------------------------------------------------------
# async-writer semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_is_non_blocking(wired_collector):
    """The tool returns before the writer commits.

    Counted via module-level ``all_tasks``: right after the tool returns a new
    pending task must be visible on the loop — that's the writer coroutine
    the decorator scheduled via ``asyncio.create_task``. If the write were
    awaited inline no such task would exist.
    """

    @monitor_tool(name="slow_write")
    async def t() -> str:
        return "done"

    tasks_before = asyncio.all_tasks()
    await t()
    tasks_after = asyncio.all_tasks()
    new_tasks = tasks_after - tasks_before
    assert new_tasks, "expected a background writer task after tool return"

    await _drain_tasks()
    post = await _fetch_events(wired_collector)
    assert len(post) == 1


# ---------------------------------------------------------------------------
# off-switch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_collector_is_passthrough(session_factory):
    configure(session_factory=session_factory, enabled=False)
    try:
        @monitor_tool()
        async def tool() -> int:
            return 42

        assert await tool() == 42
        await _drain_tasks()
        events = await _fetch_events(session_factory)
        assert events == []
    finally:
        configure(session_factory=None, enabled=True)


@pytest.mark.asyncio
async def test_unconfigured_collector_is_passthrough(session_factory):
    # Simulate the state before configure() is called from the MCP bootstrap.
    configure(session_factory=None, enabled=True)

    @monitor_tool()
    async def tool() -> str:
        return "hi"

    assert await tool() == "hi"
    await _drain_tasks()
    events = await _fetch_events(session_factory)
    assert events == []


# ---------------------------------------------------------------------------
# failure isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_writer_failure_does_not_break_tool(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
):
    """A broken session factory must never bubble up to the tool caller."""

    class _BrokenFactory:
        def __call__(self):  # mimic async_sessionmaker() call-returns-ctx
            raise RuntimeError("DB unavailable")

    configure(session_factory=_BrokenFactory(), enabled=True)  # type: ignore[arg-type]

    try:
        @monitor_tool(name="resilient")
        async def tool() -> str:
            return "still ok"

        with caplog.at_level(logging.WARNING, logger=collector.__name__):
            out = await tool()
            await _drain_tasks()

        assert out == "still ok"
        warn_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "monitor" in r.name
        ]
        assert warn_records, "expected at least one monitor warning"
    finally:
        configure(session_factory=None, enabled=True)


# ---------------------------------------------------------------------------
# actor / session_id fallbacks (Bug 1 regression coverage)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_env_actor_wins_over_default_unknown(
    wired_collector, monkeypatch: pytest.MonkeyPatch
):
    """MNEMO_ACTOR env var populates actor when no payload / ctx is present."""
    monkeypatch.setenv("MNEMO_ACTOR", "agent:claude-code")

    @monitor_tool(name="env_actor_tool")
    async def tool() -> str:
        return "ok"

    await tool()
    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    assert events[0].actor == "agent:claude-code"


@pytest.mark.asyncio
async def test_proc_fallback_session_id_is_stable(wired_collector):
    """Back-to-back calls without a ctx share the proc-level session id so
    session-aware rules (empty_streak / loop_suspect) can still aggregate."""

    @monitor_tool(name="proc_session_tool")
    async def tool() -> str:
        return "ok"

    await tool()
    await tool()
    # Two sequential SQLite commits need more yields than a single call —
    # poll until both events land or we hit the budget.
    events: list[MonitorEvent] = []
    for _ in range(50):
        await asyncio.sleep(0.01)
        events = await _fetch_events(wired_collector)
        if len(events) >= 2:
            break
    assert len(events) == 2, f"expected two events, got {events!r}"
    assert events[0].session_id is not None
    assert events[0].session_id.startswith("proc:")
    assert events[0].session_id == events[1].session_id


@pytest.mark.asyncio
async def test_record_payload_actor_overrides_env(
    wired_collector, monkeypatch: pytest.MonkeyPatch
):
    """Explicit record_payload(actor=...) must override both env and ctx —
    feedback_knowledge relies on this to persist the caller-supplied actor."""
    monkeypatch.setenv("MNEMO_ACTOR", "agent:env-set")

    @monitor_tool(name="feedback_like")
    async def tool() -> str:
        record_payload(actor="agent:caller-override", signal="helpful")
        return "ok"

    await tool()
    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    assert events[0].actor == "agent:caller-override"
    meta = json.loads(events[0].result_meta)
    assert meta == {"signal": "helpful"}


@pytest.mark.asyncio
async def test_payload_populates_result_meta(wired_collector):
    """Bug 2: record_payload(...) inside a tool must land non-null
    result_meta so auto_fallback / hits / top1_score stay observable."""

    @monitor_tool(name="search_like")
    async def tool() -> str:
        record_payload(
            hits=3,
            mode="hybrid",
            sort_by="relevance",
            auto_fallback=True,
            top1_score=0.42,
        )
        return "ok"

    await tool()
    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    assert events[0].result_meta is not None
    meta = json.loads(events[0].result_meta)
    assert meta["hits"] == 3
    assert meta["mode"] == "hybrid"
    assert meta["sort_by"] == "relevance"
    assert meta["auto_fallback"] is True
    assert meta["top1_score"] == 0.42


# ---------------------------------------------------------------------------
# error event captures exception message (Bug: monitor 异常不记消息)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_event_captures_exception_message(wired_collector):
    """When a monitored tool raises, result_summary must contain str(exc),
    not an empty string. Regression test for the 'error path drops message' bug."""

    @monitor_tool(name="err_msg_tool")
    async def tool_that_fails(x: int) -> str:
        raise RuntimeError(f"connection refused on port {x}")

    with pytest.raises(RuntimeError, match="connection refused"):
        await tool_that_fails(8080)

    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    ev = events[0]
    assert ev.status == "error"
    assert ev.error_type == "RuntimeError"
    # Core assertion: result_summary is NOT empty — it holds the exception text.
    assert ev.result_summary != ""
    assert "connection refused on port 8080" in ev.result_summary


@pytest.mark.asyncio
async def test_error_result_summary_truncated_to_500(wired_collector):
    """Exception messages longer than 500 chars are truncated."""

    long_msg = "x" * 600

    @monitor_tool(name="long_err")
    async def tool() -> str:
        raise ValueError(long_msg)

    with pytest.raises(ValueError):
        await tool()

    await _drain_tasks()
    events = await _fetch_events(wired_collector)
    assert len(events) == 1
    assert len(events[0].result_summary) == 500
