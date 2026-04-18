"""Unit tests for services/lifecycle_service.

Scope covered:
  * check_stale_transition — pure function, all branches (both conditions,
    one condition, never-accessed shortcut, flag off, unknown claim_type,
    non-active status).
  * touch_last_accessed — batch write + 60s dedupe (fresh rows skipped,
    stale rows written, empty input is a no-op).

Red-line: no mocks — check_stale_transition uses plain dataclass rows,
touch_last_accessed runs against a real aiosqlite session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mnemo.config import MnemoConfig
from mnemo.models.knowledge import Base, Knowledge
from mnemo.services.lifecycle_service import (
    check_stale_transition,
    touch_last_accessed,
)


@dataclass
class _Row:
    """Test double for a Knowledge row — check_stale_transition reads only
    attributes, so a dataclass is sufficient and keeps the pure-function
    promise (no SQLAlchemy required)."""

    status: str = "active"
    claim_type: str | None = "fact"
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None


def _utc(days_ago: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


@pytest.fixture
def cfg() -> MnemoConfig:
    return MnemoConfig(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# check_stale_transition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim_type,update_days,access_days",
    [
        ("fact", 181, 91),
        ("decision", 121, 61),
        ("procedure", 61, 31),
        ("hypothesis", 31, 15),
    ],
)
def test_both_conditions_past_threshold_returns_true(
    cfg, claim_type, update_days, access_days
):
    row = _Row(
        claim_type=claim_type,
        updated_at=_utc(days_ago=update_days),
        last_accessed_at=_utc(days_ago=access_days),
    )
    assert check_stale_transition(row, cfg) is True


@pytest.mark.parametrize("claim_type", ["fact", "decision", "procedure", "hypothesis"])
def test_only_update_past_threshold_returns_false(cfg, claim_type):
    thresholds = cfg.stale_thresholds_by_claim_type[claim_type]
    row = _Row(
        claim_type=claim_type,
        updated_at=_utc(days_ago=thresholds["no_update_days"] + 10),
        last_accessed_at=_utc(days_ago=max(0, thresholds["no_access_days"] - 5)),
    )
    assert check_stale_transition(row, cfg) is False


@pytest.mark.parametrize("claim_type", ["fact", "decision", "procedure", "hypothesis"])
def test_only_access_past_threshold_returns_false(cfg, claim_type):
    thresholds = cfg.stale_thresholds_by_claim_type[claim_type]
    row = _Row(
        claim_type=claim_type,
        updated_at=_utc(days_ago=max(0, thresholds["no_update_days"] - 5)),
        last_accessed_at=_utc(days_ago=thresholds["no_access_days"] + 10),
    )
    assert check_stale_transition(row, cfg) is False


def test_last_accessed_none_does_not_trigger_transition(cfg):
    """A None last_accessed_at is treated as "hasn't had its first read yet" —
    the read-lazy caller fills it on the current call, so the stale clock
    starts from the first real read. §6.2 "被读就会刷新活跃度"."""
    row = _Row(
        claim_type="fact",
        updated_at=_utc(days_ago=200),
        last_accessed_at=None,
    )
    assert check_stale_transition(row, cfg) is False


def test_flag_off_returns_false(cfg):
    cfg.state_machine_enabled = False
    row = _Row(
        claim_type="hypothesis",
        updated_at=_utc(days_ago=1000),
        last_accessed_at=_utc(days_ago=1000),
    )
    assert check_stale_transition(row, cfg) is False


def test_non_active_status_returns_false(cfg):
    row = _Row(
        status="superseded",
        claim_type="fact",
        updated_at=_utc(days_ago=200),
        last_accessed_at=_utc(days_ago=200),
    )
    assert check_stale_transition(row, cfg) is False


def test_unknown_claim_type_returns_false(cfg):
    row = _Row(
        claim_type="mystery",
        updated_at=_utc(days_ago=1000),
        last_accessed_at=_utc(days_ago=1000),
    )
    assert check_stale_transition(row, cfg) is False


def test_missing_claim_type_returns_false(cfg):
    row = _Row(
        claim_type=None,
        updated_at=_utc(days_ago=1000),
        last_accessed_at=_utc(days_ago=1000),
    )
    assert check_stale_transition(row, cfg) is False


def test_missing_updated_at_returns_false(cfg):
    row = _Row(
        claim_type="fact",
        updated_at=None,
        last_accessed_at=_utc(days_ago=1000),
    )
    assert check_stale_transition(row, cfg) is False


def test_naive_datetime_coerced_to_utc(cfg):
    """SQLite round-trips strip tzinfo — the function must not crash on the
    naive datetimes the ORM hands back."""
    row = _Row(
        claim_type="procedure",
        updated_at=datetime.now() - timedelta(days=70),
        last_accessed_at=datetime.now() - timedelta(days=40),
    )
    assert check_stale_transition(row, cfg) is True


def test_custom_now_parameter(cfg):
    """Passing an explicit ``now`` lets callers pin time for deterministic
    tests — verify it's honored instead of wall clock."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = _Row(
        claim_type="fact",
        updated_at=base - timedelta(days=200),
        last_accessed_at=base - timedelta(days=100),
    )
    assert check_stale_transition(row, cfg, now=base) is True
    # Viewed from a year earlier, neither condition holds.
    earlier = base - timedelta(days=365)
    assert check_stale_transition(row, cfg, now=earlier) is False


# ---------------------------------------------------------------------------
# touch_last_accessed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    db_path = tmp_path / "lifecycle.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _insert_row(
    factory: async_sessionmaker,
    *,
    last_accessed_at: datetime | None,
) -> int:
    async with factory() as session:
        row = Knowledge(
            title="t",
            tags="[]",
            summary="s",
            content="c",
            scope="global",
            claim_type="fact",
            last_accessed_at=last_accessed_at,
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.mark.asyncio
async def test_touch_empty_ids_is_noop(session_factory):
    async with session_factory() as session:
        written = await touch_last_accessed(session, [])
        await session.commit()
    assert written == []


@pytest.mark.asyncio
async def test_touch_writes_when_last_accessed_is_null(session_factory):
    kid = await _insert_row(session_factory, last_accessed_at=None)
    async with session_factory() as session:
        written = await touch_last_accessed(session, [kid])
        await session.commit()
    assert written == [kid]
    async with session_factory() as session:
        got = (
            await session.execute(
                select(Knowledge.last_accessed_at).where(Knowledge.id == kid)
            )
        ).scalar_one()
    assert got is not None


@pytest.mark.asyncio
async def test_touch_dedupes_within_60s(session_factory):
    # Seed a row whose last_accessed_at is just 5s old — inside the dedupe
    # window, so touch must NOT overwrite it.
    seed = datetime.now(timezone.utc) - timedelta(seconds=5)
    kid = await _insert_row(session_factory, last_accessed_at=seed)
    async with session_factory() as session:
        written = await touch_last_accessed(session, [kid])
        await session.commit()
    assert written == []
    async with session_factory() as session:
        got = (
            await session.execute(
                select(Knowledge.last_accessed_at).where(Knowledge.id == kid)
            )
        ).scalar_one()
    assert abs((got - seed).total_seconds()) < 1.0


@pytest.mark.asyncio
async def test_touch_writes_when_older_than_60s(session_factory):
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    kid = await _insert_row(session_factory, last_accessed_at=old)
    async with session_factory() as session:
        written = await touch_last_accessed(session, [kid])
        await session.commit()
    assert written == [kid]
    async with session_factory() as session:
        got = (
            await session.execute(
                select(Knowledge.last_accessed_at).where(Knowledge.id == kid)
            )
        ).scalar_one()
    # Overwritten with a value near "now".
    now = datetime.now(timezone.utc)
    assert abs((got - now).total_seconds()) < 5.0


@pytest.mark.asyncio
async def test_touch_mixed_batch_partial_write(session_factory):
    """A batch with both in-window and out-of-window ids should touch only
    the old ones — confirms per-row dedupe, not an all-or-nothing gate."""
    fresh_seed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
    old_seed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)
    fresh_id = await _insert_row(session_factory, last_accessed_at=fresh_seed)
    old_id = await _insert_row(session_factory, last_accessed_at=old_seed)
    null_id = await _insert_row(session_factory, last_accessed_at=None)

    async with session_factory() as session:
        written = await touch_last_accessed(session, [fresh_id, old_id, null_id])
        await session.commit()

    assert sorted(written) == sorted([old_id, null_id])


@pytest.mark.asyncio
async def test_touch_does_not_bump_updated_at(session_factory):
    """Regression: SQLAlchemy Core UPDATE still fires ``Column.onupdate`` —
    touching ``last_accessed_at`` via Core UPDATE must explicitly pin
    ``updated_at`` to its current value, otherwise every read would reset
    the freshness clock and stale transitions would never fire (§4.5).
    """
    kid = await _insert_row(session_factory, last_accessed_at=None)
    async with session_factory() as session:
        original = (
            await session.execute(
                select(Knowledge.updated_at).where(Knowledge.id == kid)
            )
        ).scalar_one()

    # Sleep long enough that any wall-clock onupdate fire would be visible
    # against the stored datetime's microsecond resolution.
    import asyncio as _asyncio

    await _asyncio.sleep(1.1)

    async with session_factory() as session:
        await touch_last_accessed(session, [kid])
        await session.commit()

    async with session_factory() as session:
        after = (
            await session.execute(
                select(Knowledge.updated_at).where(Knowledge.id == kid)
            )
        ).scalar_one()
        last_accessed = (
            await session.execute(
                select(Knowledge.last_accessed_at).where(Knowledge.id == kid)
            )
        ).scalar_one()

    # updated_at must be byte-identical — not "close enough". Any drift means
    # the onupdate hook fired.
    assert after == original, f"updated_at bumped: {original} -> {after}"
    # And the actual target column was written.
    assert last_accessed is not None


@pytest.mark.asyncio
async def test_touch_honors_explicit_now(session_factory):
    """Passing ``now`` lets the caller stamp a specific time (useful when the
    caller is coalescing multiple state changes into one logical timestamp)."""
    kid = await _insert_row(session_factory, last_accessed_at=None)
    pinned = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    async with session_factory() as session:
        written = await touch_last_accessed(session, [kid], now=pinned)
        await session.commit()
    assert written == [kid]
    async with session_factory() as session:
        got = (
            await session.execute(
                select(Knowledge.last_accessed_at).where(Knowledge.id == kid)
            )
        ).scalar_one()
    assert got.replace(tzinfo=timezone.utc) == pinned
